"""События конвейера уходят только после успешного commit (BACKEND_SPEC, раздел 11)."""

import logging
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.log_context import ApplicationIdFilter
from app.domain.authority import ApplicantAuthority
from app.domain.entities import Application, BankStatement, Consent
from app.domain.enums import (
    ApplicantRole,
    ApplicationStatus,
    AuthorityBasis,
    CompanyKind,
    ConsentType,
    SignatureStatus,
    SourceType,
)
from app.domain.events import StatusChangedEvent
from app.infra.mocks import MockFailures, SyntheticData
from app.infra.mocks.synthetic import RosfinPerson
from tests.fakes.auth import FixedClock
from tests.fakes.context import make_application, statement_from_raw
from tests.fakes.pipeline import make_pipeline
from tests.fakes.runtime import RecordingPublisher
from tests.fakes.scenarios import IVANOVA, KHLEBNY_DVOR, KOLOS, NOW
from tests.fakes.scoring import FixedScoringEngine
from tests.fakes.uow import InMemoryStore

S = ApplicationStatus
RUN_ID = uuid4()

STATEMENTS_DIR = (
    Path(__file__).resolve().parents[3] / "app" / "infra" / "mocks" / "data" / "statements"
)


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


async def _submitted(
    store: InMemoryStore,
    *,
    inn: str = KOLOS,
    kind: CompanyKind = CompanyKind.LLC,
    amount: str = "2000000",
    term_months: int = 12,
    statements: tuple[BankStatement, ...] = (),
) -> Application:
    consents = tuple(Consent(t, given_by=uuid4(), given_at=NOW) for t in ConsentType)
    app = make_application(
        inn=inn,
        kind=kind,
        amount=amount,
        term_months=term_months,
        consents=consents,
        statements=statements,
    )
    app.transition_to(S.SUBMITTED, actor_id=app.applicant_id, reason="Заявка отправлена", now=NOW)
    app.pipeline_run_id = RUN_ID
    app.pull_events()
    await store.applications.add(app)
    return app


async def test_each_status_is_published_after_its_own_commit(
    fixtures: SyntheticData,
) -> None:
    store = InMemoryStore()
    publisher = RecordingPublisher(store)
    pipeline = make_pipeline(
        uow=store.uow,
        clock=FixedClock(),
        publisher=publisher,
        scoring=FixedScoringEngine(),
        fixtures=fixtures,
        failures=MockFailures(),
    )
    app = await _submitted(store)

    await pipeline.run(app.id, RUN_ID)

    status_commits = [
        commits
        for event, commits in zip(publisher.events, publisher.commits_at_publish, strict=True)
        if isinstance(event, StatusChangedEvent)
    ]
    assert publisher.statuses() == [S.CHECKING, S.SCORING, S.APPROVED]
    assert status_commits == [1, 2, 3]


async def test_failed_commit_publishes_nothing(fixtures: SyntheticData) -> None:
    store = InMemoryStore()
    publisher = RecordingPublisher(store)
    pipeline = make_pipeline(
        uow=store.uow,
        clock=FixedClock(),
        publisher=publisher,
        scoring=FixedScoringEngine(),
        fixtures=fixtures,
        failures=MockFailures(),
    )
    app = await _submitted(store)
    store.fail_commits = True

    with pytest.raises(RuntimeError, match="commit"):
        await pipeline.run(app.id, RUN_ID)

    assert publisher.events == []


def _ivanova_statement(name: str, signature: SignatureStatus) -> BankStatement:
    return statement_from_raw((STATEMENTS_DIR / name).read_bytes(), signature)


@pytest.mark.parametrize(
    ("statements", "expected"),
    [
        (("ivanova_4m.txt",), [S.CHECKING, S.NEEDS_INFO]),
        # Загружены в одну и ту же секунду: проверяется добавленная последней.
        (("ivanova_4m.txt", "ivanova_12m_signed.txt"), [S.CHECKING, S.SCORING, S.APPROVED]),
        (("ivanova_12m_signed.txt", "ivanova_4m.txt"), [S.CHECKING, S.NEEDS_INFO]),
    ],
)
async def test_pipeline_checks_the_last_attached_statement(
    fixtures: SyntheticData, statements: tuple[str, ...], expected: list[ApplicationStatus]
) -> None:
    store = InMemoryStore()
    publisher = RecordingPublisher(store)
    pipeline = make_pipeline(
        uow=store.uow,
        clock=FixedClock(),
        publisher=publisher,
        scoring=FixedScoringEngine(),
        fixtures=fixtures,
        failures=MockFailures(),
    )
    attached = tuple(_ivanova_statement(name, SignatureStatus.VALID) for name in statements)
    assert len({s.uploaded_at for s in attached}) == 1
    # SCOPE раздел 12, сценарий 4: 3 млн на 24 мес без залога.
    app = await _submitted(
        store,
        inn=IVANOVA,
        kind=CompanyKind.IE,
        amount="3000000",
        term_months=24,
        statements=attached,
    )

    await pipeline.run(app.id, RUN_ID)

    assert publisher.statuses() == expected


async def test_pipeline_logs_carry_application_id(
    fixtures: SyntheticData, caplog: pytest.LogCaptureFixture
) -> None:
    failures = MockFailures()
    await failures.set(SourceType.FSSP, KOLOS, enabled=True)
    store = InMemoryStore()
    pipeline = make_pipeline(
        uow=store.uow,
        clock=FixedClock(),
        publisher=RecordingPublisher(store),
        scoring=FixedScoringEngine(),
        fixtures=fixtures,
        failures=failures,
    )
    app = await _submitted(store)
    caplog.handler.addFilter(ApplicationIdFilter())

    with caplog.at_level(logging.INFO):
        await pipeline.run(app.id, RUN_ID)

    source_warnings = [r for r in caplog.records if "FSSP" in r.getMessage()]
    assert source_warnings
    assert {r.__dict__["application_id"] for r in source_warnings} == {str(app.id)}
    assert ApplicationIdFilter().filter(logging.makeLogRecord({}))
    assert logging.makeLogRecord({}).__dict__.get("application_id") is None


@pytest.mark.parametrize(
    ("authority", "outcome"),
    [
        (
            ApplicantAuthority(
                role=ApplicantRole.FINANCE_OFFICER,
                basis=AuthorityBasis.POWER_OF_ATTORNEY,
                representative_inn="771502345660",
                representative_name="Васильева Анна Игоревна",
            ),
            S.REJECTED,
        ),
        (None, S.APPROVED),
    ],
)
async def test_representative_is_checked_against_rosfin_list(
    fixtures: SyntheticData, authority: ApplicantAuthority | None, outcome: ApplicationStatus
) -> None:
    # По 115-ФЗ банк проверяет и представителя клиента, а не только директора и учредителей.
    listed = RosfinPerson(
        full_name="ВАСИЛЬЕВА АННА ИГОРЕВНА",
        list_type="terrorist",
        entry_date=NOW.date(),
        reason="синтетика",
    )
    rosfin = fixtures.rosfin.model_copy(update={"persons": (*fixtures.rosfin.persons, listed)})
    data = SyntheticData(
        fixtures.companies.values(), rosfin, fixtures.passports, fixtures.mchd, fixtures.notary_poa
    )
    store = InMemoryStore()
    publisher = RecordingPublisher(store)
    pipeline = make_pipeline(
        uow=store.uow,
        clock=FixedClock(),
        publisher=publisher,
        scoring=FixedScoringEngine(),
        fixtures=data,
        failures=MockFailures(),
    )
    app = await _submitted(store, inn=KHLEBNY_DVOR)
    app.authority = authority

    await pipeline.run(app.id, RUN_ID)

    assert publisher.statuses()[-1] is outcome
