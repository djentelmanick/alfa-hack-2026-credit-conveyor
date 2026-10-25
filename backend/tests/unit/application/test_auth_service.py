from datetime import date, timedelta

import pytest

from app.application.auth_service import AuthService, Passport, Registration
from app.domain.enums import Role
from app.domain.errors import InvalidCredentials, LoginTaken, TokenInvalid, ValidationFailed
from app.infra.mocks import MockFailures, MockPassportAdapter, SyntheticData
from tests.fakes.auth import PASSWORD, TOKEN_TTL, FixedClock, fast_hasher, jwt_service, make_user
from tests.fakes.scenarios import TODAY
from tests.fakes.uow import InMemoryStore

VALID_PASSPORT = Passport(series="4510", number="654321", issued_at=date(2015, 3, 1))
# Из app/infra/mocks/data/passports.json.
INVALID_PASSPORT = Passport(series="4500", number="000000", issued_at=date(2015, 3, 1))


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def service(store: InMemoryStore, clock: FixedClock) -> AuthService:
    return AuthService(
        uow=store.uow,
        hasher=fast_hasher(),
        tokens=jwt_service(clock),
        passports=MockPassportAdapter(SyntheticData.load(), MockFailures()),
        clock=clock,
    )


def _registration(login: str = "New.Client", passport: Passport = VALID_PASSPORT) -> Registration:
    return Registration(
        login=login,
        password=PASSWORD,
        full_name="  Петров   Алексей Николаевич ",
        passport=passport,
    )


async def test_register_creates_verified_client_with_hashed_password(
    service: AuthService, store: InMemoryStore
) -> None:
    user = await service.register(_registration())

    assert user.role is Role.CLIENT
    assert user.login == "new.client"
    assert user.full_name == "Петров Алексей Николаевич"
    assert user.passport_verified
    assert user.companies == []
    assert user.password_hash != PASSWORD
    assert await fast_hasher().verify(user.password_hash, PASSWORD)
    assert await store.users.get(user.id) is user
    assert store.commits == 1


async def test_register_rejects_taken_login_ignoring_case(service: AuthService) -> None:
    await service.register(_registration("client"))

    with pytest.raises(LoginTaken):
        await service.register(_registration(" CLIENT "))


async def test_register_rejects_invalid_passport(
    service: AuthService, store: InMemoryStore
) -> None:
    with pytest.raises(ValidationFailed) as exc:
        await service.register(_registration(passport=INVALID_PASSPORT))

    assert [(d.field, d.code) for d in exc.value.details] == [("passport", "PASSPORT_INVALID")]
    assert store.users.items == {}


async def test_register_rejects_passport_issued_in_future(service: AuthService) -> None:
    passport = Passport(series="4510", number="654321", issued_at=TODAY + timedelta(days=1))

    with pytest.raises(ValidationFailed) as exc:
        await service.register(_registration(passport=passport))

    assert [d.field for d in exc.value.details] == ["passport.issued_at"]


async def test_passport_issued_today_is_accepted(service: AuthService) -> None:
    passport = Passport(series="4510", number="654321", issued_at=TODAY)

    assert (await service.register(_registration(passport=passport))).passport_verified


async def test_login_returns_token_for_user(
    service: AuthService, store: InMemoryStore, clock: FixedClock
) -> None:
    user = await make_user(login="client", role=Role.ANALYST)
    await store.users.add(user)

    token = await service.login(" Client ", PASSWORD)

    assert token.user is user
    assert token.expires_in == int(TOKEN_TTL.total_seconds())
    claims = jwt_service(clock).decode(token.access_token)
    assert (claims.user_id, claims.role) == (user.id, Role.ANALYST)


async def test_login_with_wrong_password_fails(service: AuthService, store: InMemoryStore) -> None:
    await store.users.add(await make_user(login="client"))

    with pytest.raises(InvalidCredentials):
        await service.login("client", "wrong-password")


async def test_login_with_unknown_login_fails_the_same_way(service: AuthService) -> None:
    with pytest.raises(InvalidCredentials):
        await service.login("nobody", PASSWORD)


async def test_me_returns_current_user(
    service: AuthService, store: InMemoryStore, clock: FixedClock
) -> None:
    user = await make_user()
    await store.users.add(user)
    claims = jwt_service(clock).decode(jwt_service(clock).issue(user))

    assert await service.me(claims) is user


async def test_me_for_deleted_user_means_invalid_token(
    service: AuthService, clock: FixedClock
) -> None:
    tokens = jwt_service(clock)
    claims = tokens.decode(tokens.issue(await make_user()))

    with pytest.raises(TokenInvalid):
        await service.me(claims)
