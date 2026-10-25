"""Тестовые учётки: руководитель каждой компании из SCOPE, раздел 12, и аналитик.

Запуск: python -m app.infra.mocks.seed. Пароль у всех учёток один, из SEED_PASSWORD.
Повторный запуск пропускает существующие логины.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from app.domain.entities import User, UserCompany
from app.domain.enums import Role
from app.domain.ports import Clock, CompanyRegistryPort, PasswordHasher, UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedAccount:
    login: str
    full_name: str
    role: Role
    person_inn: str | None = None
    company_inn: str | None = None


# ИНН физлиц совпадают с руководителями в синтетике ЕГРЮЛ: по ним проверяется право подать заявку.
SEED_ACCOUNTS = (
    SeedAccount("kolos", "Петров Алексей Николаевич", Role.CLIENT, "771401234587", "7714012340"),
    SeedAccount("romashka", "Смирнова Ольга Викторовна", Role.CLIENT, "770312345678", "7703123457"),
    SeedAccount(
        "sokolov", "Соколов Дмитрий Андреевич", Role.CLIENT, "770498765403", "770498765403"
    ),
    SeedAccount("ivanova", "Иванова Елена Сергеевна", Role.CLIENT, "770512345650", "770512345650"),
    SeedAccount(
        "techprom", "Кузнецов Игорь Валентинович", Role.CLIENT, "770623456765", "7704123450"
    ),
    # Представители без привязки компании: её делают сами, приложив доверенность в анкете.
    SeedAccount("buh", "Васильева Анна Игоревна", Role.CLIENT, "771502345660"),
    SeedAccount("petrova", "Петрова Мария Сергеевна", Role.CLIENT, "771402345674"),
    SeedAccount("lebedev", "Лебедев Павел Олегович", Role.CLIENT, "770734567870"),
    SeedAccount("analyst", "Орлова Мария Игоревна", Role.ANALYST),
)


class SeedOutcome(StrEnum):
    CREATED = "CREATED"
    SKIPPED = "SKIPPED"
    UPDATED = "UPDATED"


async def seed(
    *,
    uow: Callable[[], UnitOfWork],
    hasher: PasswordHasher,
    registry: CompanyRegistryPort,
    clock: Clock,
    password: str,
    accounts: tuple[SeedAccount, ...] = SEED_ACCOUNTS,
) -> dict[str, SeedOutcome]:
    password_hash = await hasher.hash(password)
    outcome: dict[str, SeedOutcome] = {}
    async with uow() as unit:
        for account in accounts:
            existing = await unit.users.get_by_login(account.login)
            if existing is not None:
                # Учётки из базы до миграции 0008 без ИНН: директоров сверяют по ИНН,
                # и без него старая демо-база перестала бы пускать их к заявке.
                if existing.inn is None and account.person_inn is not None:
                    existing.inn = account.person_inn
                    outcome[account.login] = SeedOutcome.UPDATED
                else:
                    outcome[account.login] = SeedOutcome.SKIPPED
                continue
            user = User(
                id=uuid4(),
                login=account.login,
                password_hash=password_hash,
                role=account.role,
                full_name=account.full_name,
                inn=account.person_inn,
                passport_verified=True,
                created_at=clock.now(),
            )
            if account.company_inn is not None:
                company = await registry.find_by_inn(account.company_inn)
                if not company.is_director(user):
                    raise ValueError(f"{account.login}: в ЕГРЮЛ руководитель {company.director}")
                user.link_company(
                    UserCompany(
                        inn=company.inn, name=company.name, kind=company.kind, is_director=True
                    )
                )
            await unit.users.add(user)
            outcome[account.login] = SeedOutcome.CREATED
        await unit.commit()
    return outcome


async def _main() -> None:
    from app.infra.clock import SystemClock
    from app.infra.config.settings import get_settings
    from app.infra.db.session import create_engine, create_session_factory
    from app.infra.db.uow import SqlUnitOfWork
    from app.infra.mocks import MockEgrulAdapter, MockFailures, SyntheticData
    from app.infra.security.passwords import Argon2PasswordHasher

    settings = get_settings()
    if settings.seed_password is None:
        raise SystemExit("Задайте SEED_PASSWORD: пароль тестовых учёток не хранится в коде")
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        result = await seed(
            uow=lambda: SqlUnitOfWork(session_factory),
            hasher=Argon2PasswordHasher(),
            registry=MockEgrulAdapter(SyntheticData.load(), MockFailures()),
            clock=SystemClock(),
            password=settings.seed_password.get_secret_value(),
        )
    finally:
        await engine.dispose()
    for login, status in result.items():
        logger.info("%s: %s", login, status)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_main())
