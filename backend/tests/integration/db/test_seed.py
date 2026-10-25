from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.domain.enums import Role
from app.infra.db.uow import SqlUnitOfWork
from app.infra.mocks import MockEgrulAdapter, MockFailures, SyntheticData
from app.infra.mocks.seed import SEED_ACCOUNTS, SeedAccount, SeedOutcome, seed
from tests.fakes.auth import FixedClock, fast_hasher, make_container

SEED_PASSWORD = "seed-password-for-tests"


async def _seed(uow: Callable[[], SqlUnitOfWork]) -> dict[str, SeedOutcome]:
    return await seed(
        uow=uow,
        hasher=fast_hasher(),
        registry=MockEgrulAdapter(SyntheticData.load(), MockFailures()),
        clock=FixedClock(),
        password=SEED_PASSWORD,
    )


async def test_seed_is_idempotent(uow: Callable[[], SqlUnitOfWork]) -> None:
    first = await _seed(uow)
    second = await _seed(uow)

    assert set(first.values()) == {SeedOutcome.CREATED}
    assert set(second.values()) == {SeedOutcome.SKIPPED}
    assert set(first) == {a.login for a in SEED_ACCOUNTS}


@pytest.mark.parametrize("account", SEED_ACCOUNTS, ids=lambda a: a.login)
async def test_each_seed_account_can_log_in(
    app: FastAPI, client: AsyncClient, uow: Callable[[], SqlUnitOfWork], account: SeedAccount
) -> None:
    await _seed(uow)
    container = make_container(uow, FixedClock())
    app.state.container = container
    login = account.login

    response = await client.post(
        "/api/v1/auth/login", json={"login": login, "password": SEED_PASSWORD}
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    claims = container.tokens.decode(body["access_token"])
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json() == body["user"]
    if claims.role is Role.ANALYST:
        assert body["user"]["companies"] == []
    else:
        # Руководитель видит свою компанию сразу после входа и может по ней подать заявку.
        [company] = body["user"]["companies"]
        assert company["is_director"] is True
        can_apply = await client.post(
            "/api/v1/companies/get",
            json={"inn": company["inn"]},
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert can_apply.json()["can_apply"] is True
