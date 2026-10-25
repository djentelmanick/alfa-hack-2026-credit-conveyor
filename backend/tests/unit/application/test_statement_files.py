"""Выписки 1С в ApplicationService: загрузка в S3, удаление, ссылки (BACKEND_SPEC, раздел 14)."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.application.application_service import ApplicationService, UploadedFile
from app.domain.entities import Application, TokenClaims
from app.domain.enums import ApplicationStatus, CompanyKind, Role, SignatureStatus, SourceType
from app.domain.errors import (
    FileTooLarge,
    Forbidden,
    InvalidStatus,
    NotFound,
    SourceUnavailable,
    StorageUnavailable,
    ValidationFailed,
)
from app.infra.mocks import MockFailures, SyntheticData
from tests.fakes.auth import FixedClock, make_container
from tests.fakes.context import make_application
from tests.fakes.scenarios import IVANOVA, KOLOS, NOW
from tests.fakes.storage import InMemoryFileStorage
from tests.fakes.uow import InMemoryStore

S = ApplicationStatus
BUCKET = "credit-files"
MAX_BYTES = 10 * 1024 * 1024
STATEMENTS_DIR = (
    Path(__file__).resolve().parents[3] / "app" / "infra" / "mocks" / "data" / "statements"
)
SIGNED_12M = (STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes()


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


class World:
    def __init__(self, fixtures: SyntheticData) -> None:
        self.store = InMemoryStore()
        self.storage = InMemoryFileStorage()
        self.failures = MockFailures()
        self.clock = FixedClock()
        container = make_container(
            self.store.uow,
            self.clock,
            fixtures=fixtures,
            failures=self.failures,
            storage=self.storage,
        )
        self.service: ApplicationService = container.applications

    async def application(
        self, owner: TokenClaims, *, inn: str = IVANOVA, status: ApplicationStatus = S.DRAFT
    ) -> Application:
        app = make_application(inn=inn, kind=CompanyKind.IE)
        app.applicant_id = owner.user_id
        if status is not S.DRAFT:
            app.transition_to(status, actor_id=owner.user_id, reason="тест", now=NOW)
            app.pull_events()
        await self.store.applications.add(app)
        return app


@pytest.fixture
def world(fixtures: SyntheticData) -> World:
    return World(fixtures)


def _claims(role: Role = Role.CLIENT, user_id: UUID | None = None) -> TokenClaims:
    return TokenClaims(
        user_id=user_id or uuid4(),
        role=role,
        name="Тест",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        token_id=str(uuid4()),
    )


def _file(data: bytes = SIGNED_12M, name: str = "выписка.txt") -> UploadedFile:
    return UploadedFile(file_name=name, content_type="text/plain", data=data)


async def test_upload_stores_original_under_spec_key(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    statement = await world.service.attach_statement(owner, app.id, _file())

    assert statement.file_key == f"statements/{app.id}/{statement.id}.txt"
    assert world.storage.objects[BUCKET, statement.file_key] == SIGNED_12M
    assert statement.file_name == "выписка.txt"
    assert statement.signature_status is SignatureStatus.VALID
    assert statement.account_inn == IVANOVA
    assert statement.uploaded_at == NOW
    stored = await world.store.applications.get(app.id)
    assert [s.id for s in stored.statements] == [statement.id]


async def test_file_over_limit_is_too_large(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    with pytest.raises(FileTooLarge):
        await world.service.attach_statement(owner, app.id, _file(b"x" * (MAX_BYTES + 1)))

    assert world.storage.objects == {}


async def test_file_exactly_at_limit_is_not_too_large(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    with pytest.raises(ValidationFailed) as exc:
        await world.service.attach_statement(owner, app.id, _file(b"x" * MAX_BYTES))

    assert [(d.field, d.code) for d in exc.value.details] == [("file", "STATEMENT_FORMAT")]


async def test_not_1c_file_is_rejected(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    with pytest.raises(ValidationFailed) as exc:
        await world.service.attach_statement(owner, app.id, _file(b"%PDF-1.7"))

    assert [(d.field, d.code) for d in exc.value.details] == [("file", "STATEMENT_FORMAT")]
    assert world.storage.objects == {}


async def test_statement_of_another_inn_is_rejected(world: World) -> None:
    owner = _claims()
    app = await world.application(owner, inn=KOLOS)

    with pytest.raises(ValidationFailed) as exc:
        await world.service.attach_statement(owner, app.id, _file())

    assert [(d.field, d.code) for d in exc.value.details] == [("file", "STATEMENT_INN_MATCH")]
    assert world.storage.objects == {}


async def test_other_client_cannot_touch_statements(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    statement = await world.service.attach_statement(owner, app.id, _file())
    stranger = _claims()

    with pytest.raises(Forbidden):
        await world.service.attach_statement(stranger, app.id, _file())
    with pytest.raises(Forbidden):
        await world.service.delete_statement(stranger, app.id, statement.id)
    with pytest.raises(Forbidden):
        await world.service.statement_url(stranger, app.id, statement.id)


async def test_upload_after_submit_is_invalid_status(world: World) -> None:
    owner = _claims()
    app = await world.application(owner, status=S.SUBMITTED)

    with pytest.raises(InvalidStatus):
        await world.service.attach_statement(owner, app.id, _file())

    assert world.storage.objects == {}


async def test_storage_down_attaches_nothing(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    world.storage.broken = True

    with pytest.raises(StorageUnavailable):
        await world.service.attach_statement(owner, app.id, _file())

    assert (await world.store.applications.get(app.id)).statements == []


async def test_signature_service_down_is_source_unavailable(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    world.failures.set(SourceType.BANK_SIGNATURE, IVANOVA, enabled=True)

    with pytest.raises(SourceUnavailable):
        await world.service.attach_statement(owner, app.id, _file())

    assert world.storage.objects == {}


async def test_failed_commit_removes_uploaded_file(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    world.store.fail_commits = True

    with pytest.raises(RuntimeError, match="commit"):
        await world.service.attach_statement(owner, app.id, _file())

    assert world.storage.objects == {}


async def test_delete_removes_statement_and_file(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    statement = await world.service.attach_statement(owner, app.id, _file())

    await world.service.delete_statement(owner, app.id, statement.id)

    assert (await world.store.applications.get(app.id)).statements == []
    assert world.storage.objects == {}


async def test_failed_commit_keeps_file_on_delete(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    statement = await world.service.attach_statement(owner, app.id, _file())
    world.store.fail_commits = True

    with pytest.raises(RuntimeError, match="commit"):
        await world.service.delete_statement(owner, app.id, statement.id)

    assert world.storage.keys(BUCKET) == [statement.file_key]


async def test_storage_down_after_delete_commit_is_not_an_error(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    statement = await world.service.attach_statement(owner, app.id, _file())
    world.storage.broken = True

    await world.service.delete_statement(owner, app.id, statement.id)

    assert (await world.store.applications.get(app.id)).statements == []


async def test_delete_unknown_statement_is_not_found(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    with pytest.raises(NotFound):
        await world.service.delete_statement(owner, app.id, uuid4())


async def test_owner_and_analyst_get_five_minute_link(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    statement = await world.service.attach_statement(owner, app.id, _file())

    for claims in (owner, _claims(Role.ANALYST)):
        link = await world.service.statement_url(claims, app.id, statement.id)

        assert link.url.startswith(f"http://files.test/{BUCKET}/{statement.file_key}?ttl=300")
        assert link.expires_at == NOW + timedelta(minutes=5)
        assert link.file_name == "выписка.txt"


async def test_long_names_fit_database_columns(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)
    long_bank = ("АО " + "Б" * 300).encode("cp1251")
    data = SIGNED_12M.replace('АО "СЕВЕРНЫЙ БАНК"'.encode("cp1251"), long_bank)
    name = "C:\\fakepath\\" + "в" * 300 + ".txt"

    statement = await world.service.attach_statement(owner, app.id, _file(data, name))

    assert len(statement.file_name) == 255
    assert statement.file_name.startswith("ввв")
    assert statement.file_name.endswith(".txt")
    assert len(statement.bank_name) == 255


async def test_path_is_stripped_from_file_name(world: World) -> None:
    owner = _claims()
    app = await world.application(owner)

    statement = await world.service.attach_statement(
        owner, app.id, _file(name="C:\\fakepath\\выписка.txt")
    )

    assert statement.file_name == "выписка.txt"
