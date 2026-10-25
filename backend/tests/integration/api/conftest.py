import pytest
from fastapi import FastAPI

from app.api.v1.deps import Container
from app.domain.entities import User, UserCompany
from app.domain.enums import CompanyKind, Role
from app.infra.mocks import SyntheticData
from tests.fakes.auth import FixedClock, make_container, make_user
from tests.fakes.scenarios import KOLOS
from tests.fakes.uow import InMemoryStore


@pytest.fixture(scope="session")
def synthetic() -> SyntheticData:
    return SyntheticData.load()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture(autouse=True)
def container(
    app: FastAPI, store: InMemoryStore, clock: FixedClock, synthetic: SyntheticData
) -> Container:
    # lifespan в тестах не запускается, поэтому контейнер кладём в app.state сами.
    container = make_container(store.uow, clock, fixtures=synthetic)
    app.state.container = container
    return container


@pytest.fixture
async def director(store: InMemoryStore) -> User:
    user = await make_user(
        login="kolos",
        full_name="Петров Алексей Николаевич",
        companies=(
            UserCompany(
                inn=KOLOS, name='ООО "ПЕКАРНЯ КОЛОС"', kind=CompanyKind.LLC, is_director=True
            ),
        ),
    )
    await store.users.add(user)
    return user


@pytest.fixture
async def analyst(store: InMemoryStore) -> User:
    user = await make_user(login="analyst", role=Role.ANALYST, full_name="Орлова Мария Игоревна")
    await store.users.add(user)
    return user


def bearer(container: Container, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {container.tokens.issue(user)}"}
