import datetime as dt
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.schemas.application import ActorOut
from app.api.v1.schemas.common import MoneyStr, RateStr, RequestModel
from app.application.contract_service import (
    ContractDocuments,
    ContractView,
    OperationPage,
    VersionView,
)
from app.application.emulation_service import EmulationResult
from app.domain.entities import (
    Contract,
    CreditLineContract,
    LoanContract,
    Operation,
    ScheduleItem,
    SigningCode,
    Tranche,
)
from app.domain.enums import (
    ContractAction,
    ContractStatus,
    OperationType,
    ProductCode,
    RepaymentKind,
    Variant,
)
from app.domain.value_objects import Money

COMMENT_MAX_LENGTH = 2000
SYSTEM_ACTOR: Literal["SYSTEM"] = "SYSTEM"
EMULATOR_ACTOR: Literal["EMULATOR"] = "EMULATOR"


# --- Подписание ---


class SigningConfirmIn(RequestModel):
    application_id: UUID
    code: str = Field(min_length=1, max_length=16)


class SigningCodeOut(BaseModel):
    code: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, code: SigningCode) -> Self:
        return cls(code=code.code, expires_at=code.expires_at)


class SigningConfirmOut(BaseModel):
    status: Literal["ISSUED"]
    contract_id: UUID


# --- Запросы по договору ---


class ContractListIn(RequestModel):
    application_id: UUID | None = None


class ContractIdIn(RequestModel):
    contract_id: UUID


class ContractVersionIn(RequestModel):
    contract_id: UUID
    version_no: int | None = Field(default=None, ge=1)


class ContractOperationsIn(RequestModel):
    contract_id: UUID
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class ContractUpdateIn(RequestModel):
    """Полей суммы и лимита нет намеренно: лишнее поле в теле даёт 422 (SCOPE, раздел 8)."""

    contract_id: UUID
    rate: Decimal | None = Field(default=None, gt=0, lt=100, decimal_places=2)
    term_months: int | None = Field(default=None, gt=0)
    comment: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)


class TrancheDrawIn(RequestModel):
    contract_id: UUID
    amount: Decimal = Field(gt=0, decimal_places=2)

    @property
    def money(self) -> Money:
        return Money.of(self.amount)


class TrancheRepayIn(RequestModel):
    contract_id: UUID
    tranche_id: UUID
    amount: Decimal = Field(gt=0, decimal_places=2)

    @property
    def money(self) -> Money:
        return Money.of(self.amount)


class RewindIn(RequestModel):
    contract_id: UUID
    target_date: date


class RepayIn(RequestModel):
    """amount нужен только для PARTIAL: FULL гасит весь долг."""

    contract_id: UUID
    kind: RepaymentKind
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)

    @property
    def money(self) -> Money | None:
        return Money.of(self.amount) if self.amount is not None else None


# --- Ответы ---


class TrancheOut(BaseModel):
    id: UUID
    amount: MoneyStr
    issued_at: date
    due_at: date
    outstanding: MoneyStr

    @classmethod
    def from_domain(cls, t: Tranche) -> Self:
        return cls(
            id=t.id,
            amount=t.amount.amount,
            issued_at=t.issued_at,
            due_at=t.due_at,
            outstanding=t.outstanding.amount,
        )


class NextPaymentOut(BaseModel):
    # Поле называется как тип, поэтому тип берётся через модуль: иначе аннотация увидит само поле.
    date: dt.date
    amount: MoneyStr
    principal: MoneyStr
    interest: MoneyStr


class DocumentOut(BaseModel):
    version_no: int
    file_name: str


class ContractListItemOut(BaseModel):
    id: UUID
    number: str
    product_code: ProductCode
    variant: Variant
    status: ContractStatus
    outstanding: MoneyStr
    contract_date: date

    @classmethod
    def from_domain(cls, c: Contract) -> Self:
        return cls(
            id=c.id,
            number=c.number,
            product_code=ProductCode(c.product_code),
            variant=c.variant,
            status=c.status,
            outstanding=c.debt().amount,
            contract_date=c.contract_date,
        )


class ContractOut(BaseModel):
    id: UUID
    number: str
    application_id: UUID
    product_code: ProductCode
    variant: Variant
    status: ContractStatus
    contract_date: date
    rate: RateStr
    term_months: int
    version_no: int
    document: DocumentOut | None = None
    outstanding: MoneyStr
    next_payment: NextPaymentOut | None = None
    principal: MoneyStr | None = None
    limit: MoneyStr | None = None
    available: MoneyStr | None = None
    draw_until: date | None = None
    tranches: list[TrancheOut] | None = None
    allowed_operations: list[ContractAction]

    @classmethod
    def from_view(cls, view: ContractView) -> Self:
        c = view.contract
        version = c.current_version
        out = cls(
            id=c.id,
            number=c.number,
            application_id=c.application_id,
            product_code=ProductCode(c.product_code),
            variant=c.variant,
            status=c.status,
            contract_date=c.contract_date,
            rate=c.rate,
            term_months=c.term_months,
            version_no=version.version_no,
            document=(
                DocumentOut(
                    version_no=version.version_no,
                    file_name=ContractDocuments.file_name(c, version.version_no),
                )
                if version.has_document
                else None
            ),
            outstanding=c.debt().amount,
            draw_until=view.draw_until,
            allowed_operations=list(view.allowed_operations),
        )
        if (item := view.next_payment) is not None:
            out.next_payment = NextPaymentOut(
                date=item.due_date,
                amount=item.payment.amount,
                principal=item.principal.amount,
                interest=item.interest.amount,
            )
        match c:
            case LoanContract():
                out.principal = c.principal.amount
            case CreditLineContract():
                out.limit = c.limit.amount
                out.available = c.available.amount
                out.tranches = [TrancheOut.from_domain(t) for t in c.tranches]
        return out


class ScheduleItemOut(BaseModel):
    n: int
    due_date: date
    payment: MoneyStr
    principal: MoneyStr
    interest: MoneyStr
    balance: MoneyStr

    @classmethod
    def from_domain(cls, n: int, item: ScheduleItem) -> Self:
        return cls(
            n=n,
            due_date=item.due_date,
            payment=item.payment.amount,
            principal=item.principal.amount,
            interest=item.interest.amount,
            balance=item.balance.amount,
        )


class OperationOut(BaseModel):
    id: UUID
    type: OperationType
    amount: MoneyStr
    contract_date: date
    # SYSTEM, EMULATOR или id пользователя (docs/API.md, раздел 4.6).
    actor: str
    balance_after: MoneyStr
    tranche_id: UUID | None = None

    @classmethod
    def from_domain(cls, op: Operation) -> Self:
        return cls(
            id=op.id,
            type=op.type,
            amount=op.amount.amount,
            contract_date=op.contract_date,
            actor=_operation_actor(op),
            balance_after=op.balance_after.amount,
            tranche_id=op.tranche_id,
        )


class OperationPageOut(BaseModel):
    items: list[OperationOut]
    total: int
    page: int
    size: int

    @classmethod
    def from_domain(cls, page: OperationPage) -> Self:
        return cls(
            items=[OperationOut.from_domain(op) for op in page.items],
            total=page.total,
            page=page.page,
            size=page.size,
        )


class ContractChangeOut(BaseModel):
    field: str
    old: str
    new: str


class ContractVersionOut(BaseModel):
    version_no: int
    changes: list[ContractChangeOut]
    author: ActorOut
    reason: str | None = None
    created_at: datetime
    has_document: bool

    @classmethod
    def from_view(cls, view: VersionView) -> Self:
        v = view.version
        return cls(
            version_no=v.version_no,
            changes=[ContractChangeOut(field=c.field, old=c.old, new=c.new) for c in v.changes],
            author=ActorOut(
                id=v.author_id if view.author_role is not None else None,
                role=view.author_role or SYSTEM_ACTOR,
                name=view.author_name,
            ),
            reason=v.reason,
            created_at=v.created_at,
            has_document=v.has_document,
        )


def _operation_actor(op: Operation) -> str:
    if op.emulated:
        return EMULATOR_ACTOR
    return str(op.actor_id) if op.actor_id is not None else SYSTEM_ACTOR


class EmulationResultOut(BaseModel):
    contract_date: date
    operations: list[OperationOut]
    contract: ContractOut

    @classmethod
    def from_domain(cls, result: EmulationResult) -> Self:
        return cls(
            contract_date=result.contract_date,
            operations=[OperationOut.from_domain(op) for op in result.operations],
            contract=ContractOut.from_view(result.contract),
        )
