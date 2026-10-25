from collections.abc import Callable, Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import Application, AuditEntry, Consent, Contract, User
from app.domain.enums import ApplicationStatus
from app.domain.errors import NotFound
from app.domain.ports import (
    AnalystFilter,
    ApplicationRepository,
    AuditLogRepository,
    ConsentRepository,
    ContractRepository,
    UserRepository,
)
from app.domain.state_machine import FINAL_STATUSES, OFFER_STATUSES
from app.infra.db.mappers import (
    application_from_row,
    application_to_row,
    audit_from_row,
    audit_to_row,
    consent_from_parts,
    contract_from_row,
    contract_to_row,
    user_from_row,
    user_to_row,
)
from app.infra.db.models import (
    CONTRACT_NUMBER_SEQ,
    ApplicationRow,
    AuditEntryRow,
    Base,
    ConsentRow,
    ContractRow,
    UserRow,
)


class _Tracked[D: (User, Application, Contract)]:
    """Агрегаты, прочитанные или добавленные в этой единице работы.

    Домен меняет агрегат в памяти, а репозиторий не знает, что именно изменилось.
    Поэтому при commit каждый отслеживаемый агрегат целиком переводится в строки
    и сливается с тем, что уже лежит в сессии.
    """

    def __init__(self, session: AsyncSession, to_row: Callable[[D], Base]) -> None:
        self._session = session
        self._to_row: Callable[[D], Base] = to_row
        self._new: dict[UUID, D] = {}
        self._loaded: dict[UUID, D] = {}

    def find(self, aggregate_id: UUID) -> D | None:
        return self._loaded.get(aggregate_id) or self._new.get(aggregate_id)

    def add(self, aggregate: D) -> None:
        self._new[aggregate.id] = aggregate

    def loaded(self, aggregate: D) -> D:
        self._loaded[aggregate.id] = aggregate
        return aggregate

    async def flush(self) -> None:
        for aggregate in self._new.values():
            self._session.add(self._to_row(aggregate))
        for aggregate in self._loaded.values():
            await self._session.merge(self._to_row(aggregate))

    def committed(self) -> None:
        self._loaded.update(self._new)
        self._new.clear()


class SqlUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.tracked = _Tracked[User](session, user_to_row)

    async def get(self, user_id: UUID) -> User:
        if (user := self.tracked.find(user_id)) is not None:
            return user
        row = await self._session.get(UserRow, user_id)
        if row is None:
            raise NotFound("Пользователь не найден")
        return self.tracked.loaded(user_from_row(row))

    async def get_by_login(self, login: str) -> User | None:
        row = await self._session.scalar(select(UserRow).where(UserRow.login == login))
        if row is None:
            return None
        return self.tracked.find(row.id) or self.tracked.loaded(user_from_row(row))

    async def add(self, user: User) -> None:
        self.tracked.add(user)


class SqlApplicationRepository(ApplicationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.tracked = _Tracked[Application](session, application_to_row)

    async def get(self, application_id: UUID) -> Application:
        if (app := self.tracked.find(application_id)) is not None:
            return app
        row = await self._session.get(ApplicationRow, application_id)
        if row is None:
            raise NotFound("Заявка не найдена")
        return self.tracked.loaded(application_from_row(row))

    async def add(self, application: Application) -> None:
        self.tracked.add(application)

    async def find_active_by_inn(self, inn: str) -> Application | None:
        row = await self._session.scalar(
            select(ApplicationRow)
            .where(
                ApplicationRow.inn == inn,
                ApplicationRow.status.not_in([s.value for s in FINAL_STATUSES]),
            )
            .order_by(ApplicationRow.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return self._from_row(row)

    async def list_for_applicant(
        self,
        applicant_id: UUID,
        *,
        status: ApplicationStatus | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Application], int]:
        conditions = [ApplicationRow.applicant_id == applicant_id]
        if status is not None:
            conditions.append(ApplicationRow.status == status.value)
        total = await self._session.scalar(
            select(func.count()).select_from(ApplicationRow).where(*conditions)
        )
        query = (
            select(ApplicationRow)
            .where(*conditions)
            .order_by(ApplicationRow.created_at.desc(), ApplicationRow.id)
            .offset(offset)
            .limit(limit)
        )
        rows = await self._session.scalars(query)
        return [self._from_row(row) for row in rows], total or 0

    async def list_by_statuses(self, statuses: Collection[ApplicationStatus]) -> list[Application]:
        rows = await self._session.scalars(
            select(ApplicationRow)
            .where(ApplicationRow.status.in_([s.value for s in statuses]))
            .order_by(ApplicationRow.created_at, ApplicationRow.id)
        )
        return [self._from_row(row) for row in rows]

    async def list_expired(self, now: datetime) -> list[Application]:
        rows = await self._session.scalars(
            select(ApplicationRow)
            .where(
                ApplicationRow.status.in_([s.value for s in OFFER_STATUSES]),
                ApplicationRow.expires_at <= now,
            )
            .order_by(ApplicationRow.expires_at, ApplicationRow.id)
        )
        return [self._from_row(row) for row in rows]

    async def list_for_analyst(
        self, flt: AnalystFilter, *, offset: int = 0, limit: int | None = None
    ) -> tuple[list[Application], int]:
        conditions = [ApplicationRow.status != ApplicationStatus.DRAFT.value]
        if flt.manual_queue:
            conditions.append(ApplicationRow.status == ApplicationStatus.MANUAL_REVIEW.value)
        if flt.status is not None:
            conditions.append(ApplicationRow.status == flt.status.value)
        if flt.inn is not None:
            conditions.append(ApplicationRow.inn == flt.inn)
        if flt.risk_class is not None:
            conditions.append(ApplicationRow.scoring_risk_class == flt.risk_class.value)
        total = await self._session.scalar(
            select(func.count()).select_from(ApplicationRow).where(*conditions)
        )
        order = (
            (ApplicationRow.created_at, ApplicationRow.id)
            if flt.manual_queue
            else (ApplicationRow.updated_at.desc(), ApplicationRow.id)
        )
        rows = await self._session.scalars(
            select(ApplicationRow).where(*conditions).order_by(*order).offset(offset).limit(limit)
        )
        return [self._from_row(row) for row in rows], total or 0

    def _from_row(self, row: ApplicationRow) -> Application:
        # Уже отслеживаемый агрегат мог измениться в памяти: отдаём его, а не свежую копию.
        return self.tracked.find(row.id) or self.tracked.loaded(application_from_row(row))


class SqlContractRepository(ContractRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.tracked = _Tracked[Contract](session, contract_to_row)

    async def get(self, contract_id: UUID) -> Contract:
        if (contract := self.tracked.find(contract_id)) is not None:
            return contract
        row = await self._session.get(ContractRow, contract_id)
        if row is None:
            raise NotFound("Договор не найден")
        return self.tracked.loaded(contract_from_row(row))

    async def add(self, contract: Contract) -> None:
        self.tracked.add(contract)

    async def next_sequence(self) -> int:
        value = await self._session.scalar(CONTRACT_NUMBER_SEQ.next_value())
        if value is None:
            raise RuntimeError("Последовательность номеров договоров не вернула значение")
        return int(value)

    async def list_for(
        self, *, applicant_id: UUID | None = None, application_id: UUID | None = None
    ) -> list[Contract]:
        query = select(ContractRow)
        if applicant_id is not None:
            query = query.join(
                ApplicationRow, ApplicationRow.id == ContractRow.application_id
            ).where(ApplicationRow.applicant_id == applicant_id)
        if application_id is not None:
            query = query.where(ContractRow.application_id == application_id)
        rows = await self._session.scalars(
            query.order_by(ContractRow.start_date.desc(), ContractRow.number.desc())
        )
        return [
            self.tracked.find(row.id) or self.tracked.loaded(contract_from_row(row)) for row in rows
        ]


class SqlConsentRepository(ConsentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self, inn: str, user_id: UUID) -> list[Consent]:
        rows = await self._session.scalars(
            select(ConsentRow)
            .where(ConsentRow.inn == inn, ConsentRow.given_by == user_id)
            .order_by(ConsentRow.given_at, ConsentRow.type)
        )
        return [consent_from_parts(r.type, r.given_by, r.given_at) for r in rows]

    async def add(self, inn: str, consent: Consent) -> None:
        # merge по первичному ключу (inn, given_by, type) это вставка или обновление времени.
        await self._session.merge(
            ConsentRow(
                inn=inn,
                given_by=consent.given_by,
                type=consent.type.value,
                given_at=consent.given_at,
            )
        )


class SqlAuditLogRepository(AuditLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditEntry) -> None:
        self._session.add(audit_to_row(entry))

    async def list_for(self, target_id: UUID) -> list[AuditEntry]:
        rows = await self._session.scalars(
            select(AuditEntryRow)
            .where(AuditEntryRow.target_id == target_id)
            .order_by(AuditEntryRow.at, AuditEntryRow.id)
        )
        return [audit_from_row(row) for row in rows]
