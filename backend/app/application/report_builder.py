"""Отчёт по заявке из 7 разделов SCOPE раздел 7 (BACKEND_SPEC, раздел 16)."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.application.application_service import HistoryEntry
from app.application.source_payload import turnover_from_payload
from app.domain.entities import (
    Application,
    BankStatement,
    CheckResult,
    Contract,
    Decision,
    User,
)
from app.domain.enums import (
    DecisionOutcome,
    Direction,
    FetchStatus,
    Role,
    SourceType,
)
from app.domain.scoring import ScoringResult
from app.domain.value_objects import Money, MonthlyTurnover

TOP_PAYERS_LIMIT = 5
_PCT = Decimal("0.1")


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceView:
    source: SourceType
    status: FetchStatus
    attempts: int
    fetched_at: datetime | None
    data: Mapping[str, object]


@dataclass(frozen=True, kw_only=True, slots=True)
class TurnoverMonthView:
    month: date
    incoming: Money
    outgoing: Money


@dataclass(frozen=True, kw_only=True, slots=True)
class PayerView:
    inn: str
    name: str
    share_pct: Decimal


@dataclass(frozen=True, kw_only=True, slots=True)
class AnalystDecisionView:
    actor_id: UUID
    actor_name: str
    rate: Decimal | None
    comment: str
    decided_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class DecisionSummary:
    rule_code: str
    outcome: DecisionOutcome
    manual_reasons: tuple[str, ...]
    system_rate: Decimal | None
    analyst: AnalystDecisionView | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractSummary:
    id: UUID
    number: str
    versions_count: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicationReport:
    application: Application
    audience: Role
    applicant: User
    sources: tuple[SourceView, ...] = ()
    comparisons: tuple[CheckResult, ...] = ()
    turnover: tuple[TurnoverMonthView, ...] = ()
    top_payers: tuple[PayerView, ...] = ()
    scoring: ScoringResult | None = None
    decision: DecisionSummary | None = None
    history: tuple[HistoryEntry, ...] = field(default_factory=tuple)
    contract: ContractSummary | None = None


class ReportBuilder:
    """Для клиента (audience=CLIENT) без внутренних данных: источников, сравнений и оборотов."""

    def build(
        self,
        app: Application,
        audience: Role,
        *,
        applicant: User,
        history: Sequence[HistoryEntry],
        contract: Contract | None = None,
    ) -> ApplicationReport:
        names = {e.transition.actor_id: e.actor_name for e in history}
        common = ApplicationReport(
            application=app,
            audience=audience,
            applicant=applicant,
            scoring=app.scoring,
            decision=_decision_summary(app.decisions, names),
            history=tuple(history),
            contract=_contract_summary(contract),
        )
        if audience is Role.CLIENT:
            return common

        turnover_months, payers = _turnover(app)
        return replace(
            common,
            sources=tuple(
                SourceView(
                    source=s.source,
                    status=s.status,
                    attempts=s.attempts,
                    fetched_at=s.fetched_at,
                    data=s.payload,
                )
                for s in app.snapshots
            ),
            comparisons=tuple(app.checks),
            turnover=turnover_months,
            top_payers=payers,
        )


def _decision_summary(
    decisions: Sequence[Decision], names: Mapping[UUID | None, str]
) -> DecisionSummary | None:
    """Последнее решение системы и решение аналитика после него, если он уже решил."""
    system_index = next(
        (i for i in range(len(decisions) - 1, -1, -1) if decisions[i].decided_by is None), None
    )
    if system_index is None:
        return None
    system = decisions[system_index]
    analyst = next(
        (d for d in reversed(decisions[system_index + 1 :]) if d.decided_by is not None), None
    )
    return DecisionSummary(
        rule_code=system.rule_code,
        outcome=system.outcome,
        manual_reasons=system.reasons if system.outcome is DecisionOutcome.MANUAL_REVIEW else (),
        system_rate=system.rate,
        analyst=(
            AnalystDecisionView(
                actor_id=analyst.decided_by,
                actor_name=names.get(analyst.decided_by, ""),
                rate=analyst.rate,
                comment=analyst.comment or "",
                decided_at=analyst.decided_at,
            )
            if analyst is not None and analyst.decided_by is not None
            else None
        ),
    )


def _contract_summary(contract: Contract | None) -> ContractSummary | None:
    if contract is None:
        return None
    return ContractSummary(
        id=contract.id, number=contract.number, versions_count=len(contract.versions)
    )


def _turnover(app: Application) -> tuple[tuple[TurnoverMonthView, ...], tuple[PayerView, ...]]:
    """Обороты по счёту в нашем банке, а без счёта у нас по последней загруженной выписке."""
    own = next(
        (
            t
            for s in app.snapshots
            if s.source is SourceType.TURNOVER and s.status is FetchStatus.OK
            if (t := turnover_from_payload(s.payload)) is not None and t.months
        ),
        None,
    )
    if own is not None:
        return _own_turnover(own)
    if app.statements:
        return _statement_turnover(app.statements[-1])
    return (), ()


def _own_turnover(
    turnover: MonthlyTurnover,
) -> tuple[tuple[TurnoverMonthView, ...], tuple[PayerView, ...]]:
    months = tuple(
        TurnoverMonthView(month=m.month, incoming=m.incoming, outgoing=m.outgoing)
        for m in turnover.months
    )
    by_payer: dict[str, Money] = defaultdict(Money.zero)
    names: dict[str, str] = {}
    for m in turnover.months:
        for p in m.top_payers:
            by_payer[p.inn] = by_payer[p.inn] + p.amount
            names[p.inn] = p.name
    return months, _top_payers(by_payer, names, turnover.total())


def _statement_turnover(
    statement: BankStatement,
) -> tuple[tuple[TurnoverMonthView, ...], tuple[PayerView, ...]]:
    incoming: dict[date, Money] = defaultdict(Money.zero)
    outgoing: dict[date, Money] = defaultdict(Money.zero)
    by_payer: dict[str, Money] = defaultdict(Money.zero)
    for op in statement.operations:
        month = op.date.replace(day=1)
        if op.direction is Direction.INCOMING:
            incoming[month] = incoming[month] + op.amount
            by_payer[op.counterparty_inn] = by_payer[op.counterparty_inn] + op.amount
        else:
            outgoing[month] = outgoing[month] + op.amount
    months = tuple(
        TurnoverMonthView(month=m, incoming=incoming[m], outgoing=outgoing[m])
        for m in sorted(incoming.keys() | outgoing.keys())
    )
    # В выписке 1С у операции есть только ИНН контрагента, названия плательщика нет.
    return months, _top_payers(by_payer, {}, statement.incoming_total())


def _top_payers(
    by_payer: Mapping[str, Money], names: Mapping[str, str], total: Money
) -> tuple[PayerView, ...]:
    if not total.is_positive():
        return ()
    ranked = sorted(by_payer.items(), key=lambda item: (-item[1].amount, item[0]))
    return tuple(
        PayerView(
            inn=inn,
            name=names.get(inn, ""),
            share_pct=(amount.amount * 100 / total.amount).quantize(_PCT),
        )
        for inn, amount in ranked[:TOP_PAYERS_LIMIT]
    )
