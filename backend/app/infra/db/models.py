"""Таблицы PostgreSQL. Домен эти классы не видит: перевод в обе стороны в mappers.py.

Перечисления хранятся строками, а не типами PostgreSQL: новое значение не требует миграции типа.
Части агрегатов без собственного id (история, проверки, снимки) имеют ключ (родитель, position):
при повторном сохранении строки обновляются на месте, а не удаляются и вставляются заново.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    Sequence,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 2)
RATE = Numeric(5, 2)
ENUM = String(32)
INN = String(12)

# Имена ограничений задаются явно, чтобы миграции Alembic были воспроизводимыми.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


def _children(order_by: str) -> Any:
    # selectin: в async ленивая подгрузка невозможна, агрегат читается целиком сразу.
    return relationship(cascade="all, delete-orphan", lazy="selectin", order_by=order_by)


# --- Пользователи и согласия ---


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(ENUM)
    full_name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str | None] = mapped_column(String(12), unique=True)
    passport_verified: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime]

    companies: Mapped[list[UserCompanyRow]] = _children("UserCompanyRow.position")


class UserCompanyRow(Base):
    __tablename__ = "user_companies"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    inn: Mapped[str] = mapped_column(INN, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(ENUM)
    is_director: Mapped[bool] = mapped_column(Boolean)
    authority: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ConsentRow(Base):
    __tablename__ = "consents"

    inn: Mapped[str] = mapped_column(INN, primary_key=True)
    given_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[str] = mapped_column(ENUM, primary_key=True)
    given_at: Mapped[datetime]


# --- Заявка ---


class ApplicationRow(Base):
    __tablename__ = "applications"
    __table_args__ = (Index("ix_applications_inn_status", "inn", "status"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    applicant_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    inn: Mapped[str] = mapped_column(INN)
    company_kind: Mapped[str] = mapped_column(ENUM)
    company_name: Mapped[str] = mapped_column(String(255))
    okved: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(ENUM)
    product_code: Mapped[str] = mapped_column(ENUM)
    variant: Mapped[str] = mapped_column(ENUM)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    term_months: Mapped[int] = mapped_column(Integer)
    owner_guarantee: Mapped[bool] = mapped_column(Boolean)
    authority: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    collateral_type: Mapped[str | None] = mapped_column(ENUM)
    collateral_description: Mapped[str | None] = mapped_column(Text)
    collateral_value: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(ENUM)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    accepted_offer_id: Mapped[UUID | None]
    # Без внешнего ключа: договор сам ссылается на заявку, встречная ссылка дала бы цикл.
    contract_id: Mapped[UUID | None]
    pipeline_run_id: Mapped[UUID | None]
    signing_code: Mapped[str | None] = mapped_column(String(16))
    signing_code_expires_at: Mapped[datetime | None]
    scoring_total: Mapped[int | None] = mapped_column(Integer)
    scoring_risk_class: Mapped[str | None] = mapped_column(String(1))
    scoring_debt_load: Mapped[float | None] = mapped_column(Float)

    consents: Mapped[list[ApplicationConsentRow]] = _children("ApplicationConsentRow.position")
    statements: Mapped[list[BankStatementRow]] = _children("BankStatementRow.position")
    snapshots: Mapped[list[SourceSnapshotRow]] = _children("SourceSnapshotRow.position")
    checks: Mapped[list[CheckResultRow]] = _children("CheckResultRow.position")
    scoring_factors: Mapped[list[FactorScoreRow]] = _children("FactorScoreRow.position")
    decisions: Mapped[list[DecisionRow]] = _children("DecisionRow.position")
    info_requests: Mapped[list[InfoRequestRow]] = _children("InfoRequestRow.position")
    history: Mapped[list[StatusTransitionRow]] = _children("StatusTransitionRow.position")


def _application_fk() -> Mapped[UUID]:
    return mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True)


class ApplicationConsentRow(Base):
    __tablename__ = "application_consents"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(ENUM)
    given_by: Mapped[UUID]
    given_at: Mapped[datetime]


class BankStatementRow(Base):
    __tablename__ = "bank_statements"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(255))
    file_key: Mapped[str] = mapped_column(String(512))
    bank_name: Mapped[str] = mapped_column(String(255))
    account_inn: Mapped[str] = mapped_column(INN)
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    signature_status: Mapped[str] = mapped_column(ENUM)
    uploaded_at: Mapped[datetime]

    operations: Mapped[list[StatementOperationRow]] = _children("StatementOperationRow.position")


class StatementOperationRow(Base):
    __tablename__ = "statement_operations"

    statement_id: Mapped[UUID] = mapped_column(
        ForeignKey("bank_statements.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Атрибут date затенял бы тип date в аннотациях класса, поэтому имя колонки задано отдельно.
    op_date: Mapped[date] = mapped_column("date", Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    direction: Mapped[str] = mapped_column(ENUM)
    counterparty_inn: Mapped[str] = mapped_column(String(12))


class SourceSnapshotRow(Base):
    __tablename__ = "source_snapshots"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(ENUM)
    status: Mapped[str] = mapped_column(ENUM)
    attempts: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime]


class CheckResultRow(Base):
    __tablename__ = "check_results"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(ENUM)
    expected: Mapped[str] = mapped_column(Text)
    actual: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(ENUM)


class FactorScoreRow(Base):
    __tablename__ = "scoring_factors"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(String(255))
    points: Mapped[int] = mapped_column(Integer)


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    rule_code: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(ENUM)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    decided_by: Mapped[UUID | None]
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime]

    offers: Mapped[list[OfferOptionRow]] = _children("OfferOptionRow.position")


class OfferOptionRow(Base):
    __tablename__ = "offer_options"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str] = mapped_column(ENUM)
    variant: Mapped[str] = mapped_column(ENUM)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    term_months: Mapped[int] = mapped_column(Integer)
    rate: Mapped[Decimal | None] = mapped_column(RATE)


class InfoRequestRow(Base):
    __tablename__ = "info_requests"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    items: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    item_messages: Mapped[dict[str, Any]] = mapped_column(JSONB)
    message: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID | None]
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]


class StatusTransitionRow(Base):
    __tablename__ = "status_transitions"

    application_id: Mapped[UUID] = _application_fk()
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_status: Mapped[str] = mapped_column(ENUM)
    to_status: Mapped[str] = mapped_column(ENUM)
    actor_id: Mapped[UUID | None]
    reason: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime]


# --- Договор ---


# Номера договоров сквозные: последовательность не откатывается, поэтому номер не повторится.
CONTRACT_NUMBER_SEQ = Sequence("contract_number_seq", metadata=Base.metadata)


class ContractRow(Base):
    """LoanContract и CreditLineContract в одной таблице: колонки другого вида пустые."""

    __tablename__ = "contracts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    kind: Mapped[str] = mapped_column(ENUM)
    number: Mapped[str] = mapped_column(String(64), unique=True)
    product_code: Mapped[str] = mapped_column(ENUM)
    variant: Mapped[str] = mapped_column(ENUM)
    rate: Mapped[Decimal] = mapped_column(RATE)
    term_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    contract_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(ENUM)
    principal: Mapped[Decimal | None] = mapped_column(MONEY)
    outstanding: Mapped[Decimal | None] = mapped_column(MONEY)
    limit_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    available: Mapped[Decimal | None] = mapped_column(MONEY)

    versions: Mapped[list[ContractVersionRow]] = _children("ContractVersionRow.version_no")
    operations: Mapped[list[OperationRow]] = _children("OperationRow.position")
    tranches: Mapped[list[TrancheRow]] = _children("TrancheRow.position")


class ContractVersionRow(Base):
    __tablename__ = "contract_versions"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), primary_key=True
    )
    version_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_key: Mapped[str] = mapped_column(String(512))
    changes: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[UUID | None]
    created_at: Mapped[datetime]

    schedule: Mapped[list[ScheduleItemRow]] = _children("ScheduleItemRow.position")


class ScheduleItemRow(Base):
    __tablename__ = "schedule_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_id", "version_no"],
            ["contract_versions.contract_id", "contract_versions.version_no"],
            ondelete="CASCADE",
        ),
    )

    contract_id: Mapped[UUID] = mapped_column(primary_key=True)
    version_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    due_date: Mapped[date] = mapped_column(Date)
    principal: Mapped[Decimal] = mapped_column(MONEY)
    interest: Mapped[Decimal] = mapped_column(MONEY)
    balance: Mapped[Decimal] = mapped_column(MONEY)


class OperationRow(Base):
    __tablename__ = "operations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(ENUM)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    contract_date: Mapped[date] = mapped_column(Date)
    actor_id: Mapped[UUID | None]
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    tranche_id: Mapped[UUID | None]
    emulated: Mapped[bool] = mapped_column(Boolean)


class TrancheRow(Base):
    __tablename__ = "tranches"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    issued_at: Mapped[date] = mapped_column(Date)
    due_at: Mapped[date] = mapped_column(Date)
    outstanding: Mapped[Decimal] = mapped_column(MONEY)


# --- Демо ---


class MockFailureRow(Base):
    """Режим «сломаться» у моков: общий для api и worker, поэтому в БД, а не в памяти."""

    __tablename__ = "mock_failures"

    source: Mapped[str] = mapped_column(ENUM, primary_key=True)
    inn: Mapped[str] = mapped_column(INN, primary_key=True)


# --- Аудит ---


class AuditEntryRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID] = mapped_column(index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime]
