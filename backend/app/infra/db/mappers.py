"""Перевод агрегатов домена в строки БД и обратно.

to_row строит новый граф строк из агрегата. Репозиторий отдаёт его в session.merge, который
сопоставляет строки по первичным ключам: изменённые обновляет, новые вставляет, исчезнувшие удаляет.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.domain.authority import ApplicantAuthority, PoaRequest
from app.domain.entities import (
    Application,
    AuditEntry,
    BankStatement,
    CheckResult,
    Consent,
    Contract,
    ContractChange,
    ContractVersion,
    CreditLineContract,
    Decision,
    InfoRequest,
    LoanContract,
    OfferOption,
    Operation,
    ScheduleItem,
    SigningCode,
    SourceSnapshot,
    StatementOperation,
    StatusTransition,
    Tranche,
    User,
    UserCompany,
)
from app.domain.enums import (
    ApplicantRole,
    ApplicationStatus,
    AuthorityBasis,
    CheckOutcome,
    CheckStage,
    CollateralType,
    CompanyKind,
    ConsentType,
    ContractStatus,
    DecisionOutcome,
    Direction,
    FetchStatus,
    OperationType,
    PoaKind,
    Purpose,
    RiskClass,
    Role,
    SignatureStatus,
    SourceType,
    Variant,
)
from app.domain.scoring.result import FactorScore, ScoringResult
from app.domain.value_objects import Collateral, Money, Period
from app.infra.db.models import (
    ApplicationConsentRow,
    ApplicationRow,
    AuditEntryRow,
    BankStatementRow,
    CheckResultRow,
    ContractRow,
    ContractVersionRow,
    DecisionRow,
    FactorScoreRow,
    InfoRequestRow,
    OfferOptionRow,
    OperationRow,
    ScheduleItemRow,
    SourceSnapshotRow,
    StatementOperationRow,
    StatusTransitionRow,
    TrancheRow,
    UserCompanyRow,
    UserRow,
)

LOAN = "LOAN"
CREDIT_LINE = "CREDIT_LINE"


def _money(value: Decimal) -> Money:
    return Money(value)


def _optional_money(value: Decimal | None) -> Money | None:
    return None if value is None else Money(value)


def _required[T](value: T | None, column: str) -> T:
    if value is None:
        raise ValueError(f"В БД пустая колонка {column}, обязательная для этого вида записи")
    return value


# --- Пользователь и согласия ---


def user_to_row(user: User) -> UserRow:
    return UserRow(
        id=user.id,
        login=user.login,
        password_hash=user.password_hash,
        role=user.role.value,
        full_name=user.full_name,
        inn=user.inn,
        passport_verified=user.passport_verified,
        created_at=user.created_at,
        companies=[
            UserCompanyRow(
                user_id=user.id,
                inn=c.inn,
                position=i,
                name=c.name,
                kind=c.kind.value,
                is_director=c.is_director,
                authority=authority_to_json(c.authority) if c.authority else None,
            )
            for i, c in enumerate(user.companies)
        ],
    )


def user_from_row(row: UserRow) -> User:
    return User(
        id=row.id,
        login=row.login,
        password_hash=row.password_hash,
        role=Role(row.role),
        full_name=row.full_name,
        inn=row.inn,
        passport_verified=row.passport_verified,
        created_at=row.created_at,
        companies=[
            UserCompany(
                inn=c.inn,
                name=c.name,
                kind=CompanyKind(c.kind),
                is_director=c.is_director,
                authority=authority_from_json(c.authority) if c.authority else None,
            )
            for c in row.companies
        ],
    )


def authority_to_json(authority: ApplicantAuthority) -> dict[str, Any]:
    poa = authority.poa
    return {
        "role": authority.role.value,
        "basis": authority.basis.value,
        "representative_inn": authority.representative_inn,
        "representative_name": authority.representative_name,
        "registered_founder": authority.registered_founder,
        "poa": None
        if poa is None
        else {
            "kind": poa.kind.value,
            "mchd_guid": str(poa.mchd_guid) if poa.mchd_guid else None,
            "notarized_on": poa.notarized_on.isoformat() if poa.notarized_on else None,
            "register_number": poa.register_number,
            "notary_name": poa.notary_name,
        },
    }


def authority_from_json(data: Mapping[str, Any]) -> ApplicantAuthority:
    poa = data.get("poa")
    return ApplicantAuthority(
        role=ApplicantRole(data["role"]),
        basis=AuthorityBasis(data["basis"]),
        representative_inn=data["representative_inn"],
        representative_name=data["representative_name"],
        registered_founder=data.get("registered_founder"),
        poa=None
        if poa is None
        else PoaRequest(
            kind=PoaKind(poa["kind"]),
            mchd_guid=UUID(poa["mchd_guid"]) if poa.get("mchd_guid") else None,
            notarized_on=date.fromisoformat(poa["notarized_on"])
            if poa.get("notarized_on")
            else None,
            register_number=poa.get("register_number"),
            notary_name=poa.get("notary_name"),
        ),
    )


def consent_from_parts(type_: str, given_by: UUID, given_at: datetime) -> Consent:
    return Consent(type=ConsentType(type_), given_by=given_by, given_at=given_at)


# --- Заявка ---


def application_to_row(app: Application) -> ApplicationRow:
    collateral = app.collateral
    scoring = app.scoring
    return ApplicationRow(
        id=app.id,
        applicant_id=app.applicant_id,
        inn=app.inn,
        company_kind=app.company_kind.value,
        company_name=app.company_name,
        okved=app.okved,
        purpose=app.purpose.value,
        product_code=app.product_code,
        variant=app.variant.value,
        amount=app.amount.amount,
        term_months=app.term_months,
        owner_guarantee=app.owner_guarantee,
        collateral_type=collateral.type.value if collateral else None,
        collateral_description=collateral.description if collateral else None,
        collateral_value=collateral.estimated_value.amount if collateral else None,
        status=app.status.value,
        created_at=app.created_at,
        updated_at=app.updated_at,
        expires_at=app.expires_at,
        accepted_offer_id=app.accepted_offer_id,
        contract_id=app.contract_id,
        pipeline_run_id=app.pipeline_run_id,
        signing_code=app.signing_code.code if app.signing_code else None,
        signing_code_expires_at=app.signing_code.expires_at if app.signing_code else None,
        scoring_total=scoring.total if scoring else None,
        scoring_risk_class=scoring.risk_class.value if scoring else None,
        scoring_debt_load=scoring.debt_load if scoring else None,
        consents=[
            ApplicationConsentRow(
                application_id=app.id,
                position=i,
                type=c.type.value,
                given_by=c.given_by,
                given_at=c.given_at,
            )
            for i, c in enumerate(app.consents)
        ],
        statements=[_statement_to_row(app, i, s) for i, s in enumerate(app.statements)],
        snapshots=[
            SourceSnapshotRow(
                application_id=app.id,
                position=i,
                source=s.source.value,
                status=s.status.value,
                attempts=s.attempts,
                payload=dict(s.payload),
                fetched_at=s.fetched_at,
            )
            for i, s in enumerate(app.snapshots)
        ],
        checks=[
            CheckResultRow(
                application_id=app.id,
                position=i,
                code=c.code,
                stage=c.stage.value,
                expected=c.expected,
                actual=c.actual,
                outcome=c.outcome.value,
            )
            for i, c in enumerate(app.checks)
        ],
        scoring_factors=[
            FactorScoreRow(
                application_id=app.id,
                position=i,
                code=f.code,
                label=f.label,
                value=f.value,
                points=f.points,
            )
            for i, f in enumerate(scoring.factors if scoring else ())
        ],
        decisions=[_decision_to_row(app, i, d) for i, d in enumerate(app.decisions)],
        info_requests=[
            InfoRequestRow(
                application_id=app.id,
                position=i,
                items=list(r.items),
                item_messages=dict(r.item_messages),
                message=r.message,
                created_by=r.created_by,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
            )
            for i, r in enumerate(app.info_requests)
        ],
        history=[
            StatusTransitionRow(
                application_id=app.id,
                position=i,
                from_status=t.from_status.value,
                to_status=t.to_status.value,
                actor_id=t.actor_id,
                reason=t.reason,
                at=t.at,
            )
            for i, t in enumerate(app.history)
        ],
    )


def _statement_to_row(app: Application, position: int, s: BankStatement) -> BankStatementRow:
    return BankStatementRow(
        id=s.id,
        application_id=app.id,
        position=position,
        file_name=s.file_name,
        file_key=s.file_key,
        bank_name=s.bank_name,
        account_inn=s.account_inn,
        period_from=s.period.date_from,
        period_to=s.period.date_to,
        signature_status=s.signature_status.value,
        uploaded_at=s.uploaded_at,
        operations=[
            StatementOperationRow(
                statement_id=s.id,
                position=i,
                op_date=op.date,
                amount=op.amount.amount,
                direction=op.direction.value,
                counterparty_inn=op.counterparty_inn,
            )
            for i, op in enumerate(s.operations)
        ],
    )


def _decision_to_row(app: Application, position: int, d: Decision) -> DecisionRow:
    return DecisionRow(
        id=d.id,
        application_id=app.id,
        position=position,
        rule_code=d.rule_code,
        outcome=d.outcome.value,
        rate=d.rate,
        reasons=list(d.reasons),
        decided_by=d.decided_by,
        comment=d.comment,
        decided_at=d.decided_at,
        offers=[
            OfferOptionRow(
                id=o.id,
                decision_id=d.id,
                position=i,
                product_code=o.product_code,
                variant=o.variant.value,
                amount=o.amount.amount,
                term_months=o.term_months,
                rate=o.rate,
            )
            for i, o in enumerate(d.offers)
        ],
    )


def application_from_row(row: ApplicationRow) -> Application:
    collateral = None
    if row.collateral_type is not None:
        collateral = Collateral(
            type=CollateralType(row.collateral_type),
            description=row.collateral_description or "",
            estimated_value=_money(_required(row.collateral_value, "collateral_value")),
        )
    scoring = None
    if row.scoring_total is not None:
        scoring = ScoringResult(
            total=row.scoring_total,
            risk_class=RiskClass(_required(row.scoring_risk_class, "scoring_risk_class")),
            debt_load=_required(row.scoring_debt_load, "scoring_debt_load"),
            factors=tuple(
                FactorScore(code=f.code, label=f.label, value=f.value, points=f.points)
                for f in row.scoring_factors
            ),
        )
    return Application(
        id=row.id,
        applicant_id=row.applicant_id,
        inn=row.inn,
        company_kind=CompanyKind(row.company_kind),
        company_name=row.company_name,
        okved=row.okved,
        purpose=Purpose(row.purpose),
        product_code=row.product_code,
        variant=Variant(row.variant),
        amount=_money(row.amount),
        term_months=row.term_months,
        owner_guarantee=row.owner_guarantee,
        collateral=collateral,
        status=ApplicationStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        accepted_offer_id=row.accepted_offer_id,
        contract_id=row.contract_id,
        pipeline_run_id=row.pipeline_run_id,
        signing_code=(
            SigningCode(code=row.signing_code, expires_at=row.signing_code_expires_at)
            if row.signing_code is not None and row.signing_code_expires_at is not None
            else None
        ),
        consents=[consent_from_parts(c.type, c.given_by, c.given_at) for c in row.consents],
        statements=[_statement_from_row(s) for s in row.statements],
        snapshots=[
            SourceSnapshot(
                source=SourceType(s.source),
                status=FetchStatus(s.status),
                attempts=s.attempts,
                payload=dict(s.payload),
                fetched_at=s.fetched_at,
            )
            for s in row.snapshots
        ],
        checks=[
            CheckResult(
                code=c.code,
                stage=CheckStage(c.stage),
                expected=c.expected,
                actual=c.actual,
                outcome=CheckOutcome(c.outcome),
            )
            for c in row.checks
        ],
        scoring=scoring,
        decisions=[_decision_from_row(d) for d in row.decisions],
        info_requests=[
            InfoRequest(
                items=tuple(r.items),
                item_messages=_str_mapping(r.item_messages),
                message=r.message,
                created_by=r.created_by,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
            )
            for r in row.info_requests
        ],
        history=[
            StatusTransition(
                from_status=ApplicationStatus(t.from_status),
                to_status=ApplicationStatus(t.to_status),
                actor_id=t.actor_id,
                reason=t.reason,
                at=t.at,
            )
            for t in row.history
        ],
    )


def _statement_from_row(row: BankStatementRow) -> BankStatement:
    return BankStatement(
        id=row.id,
        file_name=row.file_name,
        file_key=row.file_key,
        bank_name=row.bank_name,
        account_inn=row.account_inn,
        period=Period(row.period_from, row.period_to),
        signature_status=SignatureStatus(row.signature_status),
        operations=tuple(
            StatementOperation(
                date=op.op_date,
                amount=_money(op.amount),
                direction=Direction(op.direction),
                counterparty_inn=op.counterparty_inn,
            )
            for op in row.operations
        ),
        uploaded_at=row.uploaded_at,
    )


def _decision_from_row(row: DecisionRow) -> Decision:
    return Decision(
        id=row.id,
        rule_code=row.rule_code,
        outcome=DecisionOutcome(row.outcome),
        rate=row.rate,
        reasons=tuple(row.reasons),
        offers=tuple(
            OfferOption(
                id=o.id,
                product_code=o.product_code,
                variant=Variant(o.variant),
                amount=_money(o.amount),
                term_months=o.term_months,
                rate=_required(o.rate, "rate"),
            )
            for o in row.offers
        ),
        decided_by=row.decided_by,
        comment=row.comment,
        decided_at=row.decided_at,
    )


# --- Договор ---


def contract_to_row(contract: Contract) -> ContractRow:
    row = ContractRow(
        id=contract.id,
        application_id=contract.application_id,
        number=contract.number,
        product_code=contract.product_code,
        variant=contract.variant.value,
        rate=contract.rate,
        term_months=contract.term_months,
        start_date=contract.start_date,
        contract_date=contract.contract_date,
        status=contract.status.value,
        versions=[_version_to_row(contract, v) for v in contract.versions],
        operations=[
            OperationRow(
                id=op.id,
                contract_id=contract.id,
                position=i,
                type=op.type.value,
                amount=op.amount.amount,
                contract_date=op.contract_date,
                actor_id=op.actor_id,
                balance_after=op.balance_after.amount,
                tranche_id=op.tranche_id,
                emulated=op.emulated,
            )
            for i, op in enumerate(contract.operations)
        ],
    )
    match contract:
        case LoanContract():
            row.kind = LOAN
            row.principal = contract.principal.amount
            row.outstanding = contract.outstanding.amount
            row.tranches = []
        case CreditLineContract():
            row.kind = CREDIT_LINE
            row.limit_amount = contract.limit.amount
            row.available = contract.available.amount
            row.tranches = [
                TrancheRow(
                    id=t.id,
                    contract_id=contract.id,
                    position=i,
                    amount=t.amount.amount,
                    issued_at=t.issued_at,
                    due_at=t.due_at,
                    outstanding=t.outstanding.amount,
                )
                for i, t in enumerate(contract.tranches)
            ]
        case _:
            raise TypeError(f"Неизвестный вид договора {type(contract).__name__}")
    return row


def _version_to_row(contract: Contract, v: ContractVersion) -> ContractVersionRow:
    return ContractVersionRow(
        contract_id=contract.id,
        version_no=v.version_no,
        document_key=v.document_key,
        changes=[{"field": c.field, "old": c.old, "new": c.new} for c in v.changes],
        reason=v.reason,
        author_id=v.author_id,
        created_at=v.created_at,
        schedule=[
            ScheduleItemRow(
                contract_id=contract.id,
                version_no=v.version_no,
                position=i,
                due_date=item.due_date,
                principal=item.principal.amount,
                interest=item.interest.amount,
                balance=item.balance.amount,
            )
            for i, item in enumerate(v.schedule)
        ],
    )


def contract_from_row(row: ContractRow) -> Contract:
    common: dict[str, Any] = {
        "id": row.id,
        "application_id": row.application_id,
        "number": row.number,
        "product_code": row.product_code,
        "variant": Variant(row.variant),
        "rate": row.rate,
        "term_months": row.term_months,
        "start_date": row.start_date,
        "contract_date": row.contract_date,
        "status": ContractStatus(row.status),
        "versions": [_version_from_row(v) for v in row.versions],
        "operations": [
            Operation(
                id=op.id,
                type=OperationType(op.type),
                amount=_money(op.amount),
                contract_date=op.contract_date,
                actor_id=op.actor_id,
                balance_after=_money(op.balance_after),
                tranche_id=op.tranche_id,
                emulated=op.emulated,
            )
            for op in row.operations
        ],
    }
    if row.kind == LOAN:
        return LoanContract(
            **common,
            principal=_money(_required(row.principal, "principal")),
            outstanding=_money(_required(row.outstanding, "outstanding")),
        )
    if row.kind == CREDIT_LINE:
        return CreditLineContract(
            **common,
            limit=_money(_required(row.limit_amount, "limit_amount")),
            available=_money(_required(row.available, "available")),
            tranches=[
                Tranche(
                    id=t.id,
                    amount=_money(t.amount),
                    issued_at=t.issued_at,
                    due_at=t.due_at,
                    outstanding=_money(t.outstanding),
                )
                for t in row.tranches
            ],
        )
    raise ValueError(f"Неизвестный вид договора в БД: {row.kind}")


def _version_from_row(row: ContractVersionRow) -> ContractVersion:
    return ContractVersion(
        version_no=row.version_no,
        document_key=row.document_key,
        changes=tuple(ContractChange(c["field"], c["old"], c["new"]) for c in row.changes),
        reason=row.reason,
        schedule=tuple(
            ScheduleItem(
                due_date=item.due_date,
                principal=_money(item.principal),
                interest=_money(item.interest),
                balance=_money(item.balance),
            )
            for item in row.schedule
        ),
        author_id=row.author_id,
        created_at=row.created_at,
    )


# --- Аудит ---


def audit_to_row(entry: AuditEntry) -> AuditEntryRow:
    return AuditEntryRow(
        id=entry.id,
        actor_id=entry.actor_id,
        action=entry.action,
        target_id=entry.target_id,
        details=dict(entry.details),
        at=entry.at,
    )


def audit_from_row(row: AuditEntryRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        actor_id=row.actor_id,
        action=row.action,
        target_id=row.target_id,
        details=_str_mapping(row.details),
        at=row.at,
    )


def _str_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in raw.items()}
