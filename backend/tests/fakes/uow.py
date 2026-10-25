"""Единица работы в памяти для тестов сервисов и HTTP без PostgreSQL.

Транзакций нет: изменения видны сразу. Тесты этих слоёв проверяют логику, а не атомарность;
она проверяется в tests/integration/db на настоящей базе.
"""

from collections import defaultdict
from uuid import UUID

from app.domain.entities import AuditEntry, Consent, Contract, User
from app.domain.errors import NotFound
from app.domain.ports import (
    AuditLogRepository,
    ConsentRepository,
    ContractRepository,
    UnitOfWork,
    UserRepository,
)
from tests.fakes.repositories import InMemoryApplicationRepository


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self.items: dict[UUID, User] = {}

    async def get(self, user_id: UUID) -> User:
        try:
            return self.items[user_id]
        except KeyError:
            raise NotFound("Пользователь не найден") from None

    async def get_by_login(self, login: str) -> User | None:
        return next((u for u in self.items.values() if u.login == login), None)

    async def get_by_inn(self, inn: str) -> User | None:
        return next((u for u in self.items.values() if u.inn == inn), None)

    async def add(self, user: User) -> None:
        self.items[user.id] = user


class InMemoryConsentRepository(ConsentRepository):
    def __init__(self) -> None:
        self.items: dict[tuple[str, UUID], dict[str, Consent]] = defaultdict(dict)

    async def list_for(self, inn: str, user_id: UUID) -> list[Consent]:
        return list(self.items[(inn, user_id)].values())

    async def add(self, inn: str, consent: Consent) -> None:
        self.items[(inn, consent.given_by)][consent.type] = consent


class InMemoryContractRepository(ContractRepository):
    def __init__(self, applications: InMemoryApplicationRepository) -> None:
        self.items: dict[UUID, Contract] = {}
        self._applications = applications
        self._sequence = 0

    async def get(self, contract_id: UUID) -> Contract:
        try:
            return self.items[contract_id]
        except KeyError:
            raise NotFound("Договор не найден") from None

    async def add(self, contract: Contract) -> None:
        self.items[contract.id] = contract

    async def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def list_for(
        self, *, applicant_id: UUID | None = None, application_id: UUID | None = None
    ) -> list[Contract]:
        found = []
        for contract in self.items.values():
            if application_id is not None and contract.application_id != application_id:
                continue
            if applicant_id is not None:
                app = await self._applications.get(contract.application_id)
                if app.applicant_id != applicant_id:
                    continue
            found.append(contract)
        return sorted(found, key=lambda c: (c.start_date, c.number), reverse=True)


class InMemoryAuditLog(AuditLogRepository):
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    async def list_for(self, target_id: UUID) -> list[AuditEntry]:
        return [e for e in self.entries if e.target_id == target_id]


class InMemoryStore:
    """Общие данные для всех единиц работы одного теста."""

    def __init__(self) -> None:
        self.applications = InMemoryApplicationRepository()
        self.contracts = InMemoryContractRepository(self.applications)
        self.users = InMemoryUserRepository()
        self.consents = InMemoryConsentRepository()
        self.audit = InMemoryAuditLog()
        self.commits = 0
        self.fail_commits = False

    def uow(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork(UnitOfWork):
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self.applications = store.applications
        self.contracts = store.contracts
        self.users = store.users
        self.consents = store.consents
        self.audit = store.audit

    async def commit(self) -> None:
        if self._store.fail_commits:
            raise RuntimeError("commit не прошёл")
        self._store.commits += 1

    async def rollback(self) -> None:
        pass
