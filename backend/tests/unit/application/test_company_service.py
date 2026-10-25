import pytest

from app.application.company_service import ANALYST_CANNOT_APPLY, CompanyService
from app.domain.entities import TokenClaims
from app.domain.enums import Role
from app.domain.errors import NotDirector, NotFound
from app.domain.value_objects import Money
from app.infra.mocks import MockEgrulAdapter, MockFailures, MockTurnoverAdapter, SyntheticData
from tests.fakes.auth import FixedClock, jwt_service, make_user
from tests.fakes.scenarios import IVANOVA, KOLOS
from tests.fakes.uow import InMemoryStore


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def service(store: InMemoryStore) -> CompanyService:
    fixtures, failures = SyntheticData.load(), MockFailures()
    return CompanyService(
        uow=store.uow,
        registry=MockEgrulAdapter(fixtures, failures),
        turnover=MockTurnoverAdapter(fixtures, failures),
    )


async def _claims(store: InMemoryStore, role: Role, full_name: str) -> TokenClaims:
    user = await make_user(role=role, full_name=full_name)
    await store.users.add(user)
    tokens = jwt_service(FixedClock())
    return tokens.decode(tokens.issue(user))


async def test_director_sees_bank_data_and_can_apply(
    service: CompanyService, store: InMemoryStore
) -> None:
    claims = await _claims(store, Role.CLIENT, "Петров Алексей Николаевич")

    view = await service.get(claims, KOLOS)

    assert view.profile.inn == KOLOS
    assert view.profile.okved_label
    assert view.has_account is True
    # SCOPE, раздел 12: выручка «Пекарни Колос» 24 млн ₽ в год.
    assert view.annual_revenue == Money.of(24_000_000)
    assert (view.can_apply, view.cannot_apply_reason) == (True, None)


async def test_not_director_sees_only_registry_data(
    service: CompanyService, store: InMemoryStore
) -> None:
    claims = await _claims(store, Role.CLIENT, "Бухгалтер Анна Петровна")

    view = await service.get(claims, KOLOS)

    assert view.profile.name
    assert (view.has_account, view.annual_revenue) == (None, None)
    assert (view.can_apply, view.cannot_apply_reason) == (False, NotDirector.default_message)


async def test_analyst_sees_bank_data_but_does_not_apply(
    service: CompanyService, store: InMemoryStore
) -> None:
    claims = await _claims(store, Role.ANALYST, "Орлова Мария Игоревна")

    view = await service.get(claims, KOLOS)

    assert view.has_account is True
    assert view.annual_revenue == Money.of(24_000_000)
    assert (view.can_apply, view.cannot_apply_reason) == (False, ANALYST_CANNOT_APPLY)


async def test_company_without_account_has_no_revenue(
    service: CompanyService, store: InMemoryStore
) -> None:
    claims = await _claims(store, Role.CLIENT, "Иванова Елена Сергеевна")

    view = await service.get(claims, IVANOVA)

    assert (view.has_account, view.annual_revenue, view.can_apply) == (False, None, True)


async def test_unknown_inn_is_not_found(service: CompanyService, store: InMemoryStore) -> None:
    claims = await _claims(store, Role.ANALYST, "Орлова Мария Игоревна")

    with pytest.raises(NotFound):
        await service.get(claims, "7700000000")
