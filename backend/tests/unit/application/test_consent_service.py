from datetime import timedelta

import pytest

from app.application.consent_service import ConsentService
from app.domain.entities import TokenClaims, User, UserCompany
from app.domain.enums import CompanyKind, ConsentType
from app.domain.errors import NotDirector, NotFound, ValidationFailed
from app.infra.mocks import MockEgrulAdapter, MockFailures, SyntheticData
from tests.fakes.auth import FixedClock, jwt_service, make_user
from tests.fakes.scenarios import KOLOS, NOW, SOKOLOV
from tests.fakes.uow import InMemoryStore

C = ConsentType


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def service(store: InMemoryStore, clock: FixedClock) -> ConsentService:
    registry = MockEgrulAdapter(SyntheticData.load(), MockFailures())
    return ConsentService(uow=store.uow, registry=registry, clock=clock)


async def _client(store: InMemoryStore, full_name: str) -> tuple[User, TokenClaims]:
    user = await make_user(full_name=full_name)
    await store.users.add(user)
    tokens = jwt_service(FixedClock())
    return user, tokens.decode(tokens.issue(user))


async def test_director_gives_consents_and_company_is_linked(
    service: ConsentService, store: InMemoryStore
) -> None:
    user, claims = await _client(store, "Петров Алексей Николаевич")

    given = await service.give(claims, KOLOS, [C.BKI_COMPANY, C.BKI_PERSONAL, C.PERSONAL_DATA])

    assert [c.type for c in given] == [C.BKI_COMPANY, C.BKI_PERSONAL, C.PERSONAL_DATA]
    assert all(c.given_by == user.id and c.given_at == NOW for c in given)
    assert await service.list_for(claims, KOLOS) == given
    assert user.companies == [
        UserCompany(inn=KOLOS, name='ООО "ПЕКАРНЯ КОЛОС"', kind=CompanyKind.LLC, is_director=True)
    ]


async def test_repeated_consent_keeps_original_time(
    service: ConsentService, store: InMemoryStore, clock: FixedClock
) -> None:
    user, claims = await _client(store, "Петров Алексей Николаевич")
    await service.give(claims, KOLOS, [C.PERSONAL_DATA])
    clock.advance(timedelta(days=1))

    given = await service.give(claims, KOLOS, [C.PERSONAL_DATA, C.BKI_PERSONAL, C.BKI_PERSONAL])

    assert [(c.type, c.given_at) for c in given] == [
        (C.PERSONAL_DATA, NOW),
        (C.BKI_PERSONAL, NOW + timedelta(days=1)),
    ]
    assert len(user.companies) == 1


async def test_not_director_cannot_give_consents(
    service: ConsentService, store: InMemoryStore
) -> None:
    user, claims = await _client(store, "Бухгалтер Анна Петровна")

    with pytest.raises(NotDirector):
        await service.give(claims, KOLOS, [C.PERSONAL_DATA])

    assert await service.list_for(claims, KOLOS) == []
    assert user.companies == []


async def test_individual_entrepreneur_cannot_give_company_bki_consent(
    service: ConsentService, store: InMemoryStore
) -> None:
    _, claims = await _client(store, "Соколов Дмитрий Андреевич")

    with pytest.raises(ValidationFailed) as exc:
        await service.give(claims, SOKOLOV, [C.BKI_PERSONAL, C.BKI_COMPANY])

    assert [(d.field, d.code) for d in exc.value.details] == [("types", "CONSENT_NOT_APPLICABLE")]


async def test_unknown_inn_is_not_found(service: ConsentService, store: InMemoryStore) -> None:
    _, claims = await _client(store, "Петров Алексей Николаевич")

    with pytest.raises(NotFound):
        await service.give(claims, "7700000000", [C.PERSONAL_DATA])


async def test_consents_are_per_user(service: ConsentService, store: InMemoryStore) -> None:
    _, director = await _client(store, "Петров Алексей Николаевич")
    _, other = await _client(store, "Петров Алексей Николаевич")
    await service.give(director, KOLOS, [C.PERSONAL_DATA])

    assert await service.list_for(other, KOLOS) == []
