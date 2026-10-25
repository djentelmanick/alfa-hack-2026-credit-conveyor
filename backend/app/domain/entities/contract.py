from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.dates import add_months, full_months_between
from app.domain.enums import ContractStatus, OperationType, Variant
from app.domain.errors import FieldError, InvalidStatus, NotFound, ValidationFailed
from app.domain.value_objects import Money

CONTRACT_NUMBER_PREFIX = "КД"
PREPAYMENT_REASON = "Частичное досрочное погашение"
MONTHS_IN_YEAR_PCT = 1200


def contract_number(year: int, sequence: int) -> str:
    return f"{CONTRACT_NUMBER_PREFIX}-{year}-{sequence:06d}"


def format_rate(rate: Decimal) -> str:
    text = format(rate.normalize(), "f")
    return text if "." in text else f"{text}.0"


@dataclass(frozen=True, kw_only=True, slots=True)
class ScheduleItem:
    due_date: date
    principal: Money
    interest: Money
    balance: Money

    @property
    def payment(self) -> Money:
        return self.principal + self.interest


@dataclass(kw_only=True, slots=True)
class Tranche:
    id: UUID
    amount: Money
    issued_at: date
    due_at: date
    outstanding: Money


@dataclass(frozen=True, slots=True)
class ContractChange:
    field: str
    old: str
    new: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractVersion:
    version_no: int
    document_key: str
    changes: tuple[ContractChange, ...] = ()
    reason: str | None = None
    schedule: tuple[ScheduleItem, ...] = ()
    author_id: UUID | None
    created_at: datetime

    @property
    def has_document(self) -> bool:
        return bool(self.document_key)


@dataclass(frozen=True, kw_only=True, slots=True)
class Operation:
    id: UUID
    type: OperationType
    amount: Money
    contract_date: date
    actor_id: UUID | None
    balance_after: Money
    tranche_id: UUID | None = None
    emulated: bool = False


@dataclass(kw_only=True, eq=False)
class Contract(ABC):
    id: UUID
    application_id: UUID
    number: str
    product_code: str
    variant: Variant
    rate: Decimal
    term_months: int
    start_date: date
    contract_date: date
    status: ContractStatus = ContractStatus.ACTIVE
    versions: list[ContractVersion] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)

    @property
    def end_date(self) -> date:
        return add_months(self.start_date, self.term_months)

    @property
    def current_version(self) -> ContractVersion:
        if not self.versions:
            raise ValueError(f"У договора {self.number} нет ни одной версии")
        return self.versions[-1]

    @property
    def next_version_no(self) -> int:
        return len(self.versions) + 1

    def version(self, version_no: int | None = None) -> ContractVersion | None:
        """Без номера это последняя версия."""
        if version_no is None:
            return self.current_version
        return next((v for v in self.versions if v.version_no == version_no), None)

    def is_active(self) -> bool:
        return self.status is ContractStatus.ACTIVE

    def ensure_active(self) -> None:
        if not self.is_active():
            raise InvalidStatus("Договор закрыт")

    @abstractmethod
    def debt(self) -> Money: ...

    @abstractmethod
    def _on_date(self, previous: date, *, month_passed: bool) -> None:
        """События дня contract_date: всё, что наступило после previous."""

    @abstractmethod
    def _event_dates(self) -> Iterator[date]:
        """Даты, когда у договора что-то наступает: плановые платежи, сроки траншей."""

    @abstractmethod
    def _clear_debt(self) -> None: ...

    @abstractmethod
    def _restore_issued_balances(self) -> None: ...

    def advance_month(self) -> list[Operation]:
        """Месячная дата считается от start_date, чтобы 31-е число не съезжало на 28-е."""
        elapsed = full_months_between(self.start_date, self.contract_date)
        return self.move_date(add_months(self.start_date, elapsed + 1))

    def move_date(self, day: date) -> list[Operation]:
        """Двигает только дату этого договора: конвейер заявок живёт по реальному времени."""
        self.ensure_active()
        if day < self.contract_date:
            raise ValueError(f"Дату договора {self.number} нельзя сдвинуть назад")
        before = len(self.operations)
        for step, month_passed in self._steps_until(day):
            previous = self.contract_date
            self.contract_date = step
            self._on_date(previous, month_passed=month_passed)
            if not self.is_active():
                break
        return self.operations[before:]

    def _steps_until(self, day: date) -> Iterator[tuple[date, bool]]:
        """Даты из (contract_date, day]: месячные от start_date, даты событий договора и сам day.

        Событие между месячными датами (срок транша, взятого после перемотки на середину месяца)
        отдельный шаг: иначе оно записалось бы задним числом на следующую месячную дату.
        """
        months: set[date] = set()
        month = full_months_between(self.start_date, self.contract_date) + 1
        while (step := add_months(self.start_date, month)) <= day:
            months.add(step)
            month += 1
        events = {d for d in self._event_dates() if self.contract_date < d <= day}
        for step in sorted(months | events | {day}):
            if step > self.contract_date:
                yield step, step in months

    def repay_full(self, actor_id: UUID) -> Operation:
        self.ensure_active()
        amount = self.debt()
        self._clear_debt()
        operation = self.record(OperationType.FULL_REPAYMENT, amount, actor_id=actor_id)
        self.status = ContractStatus.CLOSED
        return operation

    def reset_to_issued(self) -> list[str]:
        """Состояние сразу после выдачи. Возвращает ключи PDF удалённых версий."""
        later = self.versions[1:]
        for version in reversed(later):
            for change in version.changes:
                if change.field == "rate":
                    self.rate = Decimal(change.old)
                elif change.field == "term_months":
                    self.term_months = int(change.old)
        del self.versions[1:]
        del self.operations[1:]
        self.contract_date = self.start_date
        self.status = ContractStatus.ACTIVE
        self._restore_issued_balances()
        return [v.document_key for v in later if v.has_document]

    def record(
        self,
        type: OperationType,
        amount: Money,
        *,
        actor_id: UUID | None,
        tranche_id: UUID | None = None,
        emulated: bool = False,
    ) -> Operation:
        operation = Operation(
            id=uuid4(),
            type=type,
            amount=amount,
            contract_date=self.contract_date,
            actor_id=actor_id,
            balance_after=self.debt(),
            tranche_id=tranche_id,
            emulated=emulated,
        )
        self.operations.append(operation)
        return operation

    def add_version(
        self,
        *,
        document_key: str,
        changes: Sequence[ContractChange] = (),
        reason: str | None,
        schedule: Sequence[ScheduleItem],
        author_id: UUID | None,
        now: datetime,
    ) -> ContractVersion:
        version = ContractVersion(
            version_no=self.next_version_no,
            document_key=document_key,
            changes=tuple(changes),
            reason=reason,
            schedule=tuple(schedule),
            author_id=author_id,
            created_at=now,
        )
        self.versions.append(version)
        return version

    def edit_terms(
        self,
        *,
        rate: Decimal,
        term_months: int,
        schedule: Sequence[ScheduleItem],
        document_key: str,
        reason: str,
        author_id: UUID,
        now: datetime,
    ) -> ContractVersion:
        """Правка ставки или срока это допсоглашение: новая версия и запись в журнале операций."""
        self.ensure_active()
        changes = []
        if rate != self.rate:
            changes.append(ContractChange("rate", format_rate(self.rate), format_rate(rate)))
        if term_months != self.term_months:
            changes.append(ContractChange("term_months", str(self.term_months), str(term_months)))
        if not changes:
            raise ValidationFailed(
                [FieldError("rate", "NO_CHANGES", "Новые условия совпадают с действующими")]
            )
        self.rate = rate
        self.term_months = term_months
        version = self.add_version(
            document_key=document_key,
            changes=changes,
            reason=reason,
            schedule=schedule,
            author_id=author_id,
            now=now,
        )
        self.record(OperationType.CONTRACT_EDITED, Money.zero(), actor_id=author_id)
        return version


@dataclass(kw_only=True, eq=False)
class LoanContract(Contract):
    principal: Money
    outstanding: Money

    def debt(self) -> Money:
        return self.outstanding

    def next_payment(self) -> ScheduleItem | None:
        return next(
            (i for i in self.current_version.schedule if i.due_date > self.contract_date), None
        )

    def prepay(
        self,
        amount: Money,
        *,
        rebuild: Callable[[LoanContract], Sequence[ScheduleItem]],
        document_key: str,
        author_id: UUID,
        now: datetime,
    ) -> ContractVersion:
        """Долг меньше, график пересчитан на остаток (платёж меньше, срок прежний), новая версия.

        График строит продукт, поэтому он передаётся функцией: её вызывают после уменьшения долга.
        """
        self.ensure_active()
        if amount >= self.outstanding:
            raise ValidationFailed(
                [
                    FieldError(
                        "amount",
                        "AMOUNT_NOT_LESS_THAN_DEBT",
                        "Сумма не меньше остатка долга: для этого есть полное погашение",
                    )
                ]
            )
        old = self.outstanding
        self.outstanding -= amount
        self.record(OperationType.PARTIAL_PREPAYMENT, amount, actor_id=author_id)
        return self.add_version(
            document_key=document_key,
            changes=[ContractChange("outstanding", str(old), str(self.outstanding))],
            reason=PREPAYMENT_REASON,
            schedule=rebuild(self),
            author_id=author_id,
            now=now,
        )

    def _on_date(self, previous: date, *, month_passed: bool) -> None:
        schedule = self.current_version.schedule
        for n, item in enumerate(schedule, start=1):
            if not previous < item.due_date <= self.contract_date:
                continue
            # Последний платёж добирает весь остаток: иначе копейки округлений не закроют договор.
            principal = (
                self.outstanding if n == len(schedule) else min(item.principal, self.outstanding)
            )
            self.outstanding -= principal
            self.record(
                OperationType.SCHEDULED_PAYMENT,
                principal + item.interest,
                actor_id=None,
                emulated=True,
            )
        if self.outstanding.is_zero():
            self.status = ContractStatus.CLOSED

    def _event_dates(self) -> Iterator[date]:
        return (item.due_date for item in self.current_version.schedule)

    def _clear_debt(self) -> None:
        self.outstanding = Money.zero()

    def _restore_issued_balances(self) -> None:
        self.outstanding = self.principal


@dataclass(kw_only=True, eq=False)
class CreditLineContract(Contract):
    limit: Money
    available: Money
    tranches: list[Tranche] = field(default_factory=list)

    def debt(self) -> Money:
        return sum((t.outstanding for t in self.tranches), Money.zero())

    def draw(self, amount: Money, *, due_at: date, actor_id: UUID) -> Tranche:
        """Границы транша проверяет продукт (draw_errors), здесь только инвариант лимита."""
        self.ensure_active()
        if amount > self.available:
            raise ValueError(f"Транш больше доступного остатка по договору {self.number}")
        tranche = Tranche(
            id=uuid4(),
            amount=amount,
            issued_at=self.contract_date,
            due_at=due_at,
            outstanding=amount,
        )
        self.tranches.append(tranche)
        self.available -= amount
        self.record(OperationType.TRANCHE_DRAWN, amount, actor_id=actor_id, tranche_id=tranche.id)
        return tranche

    def repay_tranche(
        self, tranche_id: UUID, amount: Money, *, actor_id: UUID | None, emulated: bool = False
    ) -> Operation:
        self.ensure_active()
        tranche = next((t for t in self.tranches if t.id == tranche_id), None)
        if tranche is None:
            raise NotFound("Такого транша в договоре нет")
        if amount > tranche.outstanding:
            raise ValidationFailed(
                [FieldError("amount", "OVER_TRANCHE_DEBT", "Сумма больше долга по траншу")]
            )
        tranche.outstanding -= amount
        self.available += amount
        return self.record(
            OperationType.TRANCHE_REPAID,
            amount,
            actor_id=actor_id,
            tranche_id=tranche.id,
            emulated=emulated,
        )

    def _on_date(self, previous: date, *, month_passed: bool) -> None:
        """Проценты платятся ежемесячно, основной долг транша гасится в конце его срока."""
        if month_passed:
            for t in self.tranches:
                if t.outstanding.is_positive() and t.issued_at < self.contract_date:
                    interest = Money(t.outstanding.amount * self.rate / MONTHS_IN_YEAR_PCT)
                    self.record(
                        OperationType.INTEREST_ACCRUED,
                        interest,
                        actor_id=None,
                        tranche_id=t.id,
                        emulated=True,
                    )
        for t in self.tranches:
            if t.outstanding.is_positive() and t.due_at <= self.contract_date:
                self.repay_tranche(t.id, t.outstanding, actor_id=None, emulated=True)

    def _event_dates(self) -> Iterator[date]:
        return (t.due_at for t in self.tranches if t.outstanding.is_positive())

    def _clear_debt(self) -> None:
        for t in self.tranches:
            t.outstanding = Money.zero()
        self.available = self.limit

    def _restore_issued_balances(self) -> None:
        self.tranches.clear()
        self.available = self.limit
