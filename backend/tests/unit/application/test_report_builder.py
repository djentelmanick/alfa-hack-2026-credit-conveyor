"""Отчёт по заявке: 7 разделов SCOPE раздел 7 и урезанная версия для клиента."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.application.application_service import HistoryEntry
from app.application.report_builder import ReportBuilder
from app.application.source_payload import to_payload, turnover_from_payload
from app.domain.entities import Application, CheckResult, StatementOperation, User
from app.domain.enums import (
    CheckOutcome,
    CheckStage,
    DecisionOutcome,
    Direction,
    FetchStatus,
    RiskClass,
    Role,
    SourceType,
)
from app.domain.scoring import FactorScore, ScoringResult
from app.domain.value_objects import Money, MonthlyTurnover, MonthRevenue, Payer
from tests.fakes.analyst import manual_review_application
from tests.fakes.auth import make_user
from tests.fakes.context import make_statement
from tests.fakes.scenarios import NOW

ANALYST_ID = uuid4()

OWN_TURNOVER = MonthlyTurnover(
    inn="7704123450",
    months=(
        MonthRevenue(
            month=date(2026, 8, 1),
            incoming=Money.of(4_000_000),
            outgoing=Money.of(3_000_000),
            top_payers=(
                Payer("7714023454", 'АО "МАШЗАВОД"', Money.of(1_200_000), Decimal("30.0")),
                Payer("7705123452", 'ООО "ПРОМКОМПЛЕКТ"', Money.of(800_000), Decimal("20.0")),
            ),
        ),
        MonthRevenue(
            month=date(2026, 9, 1),
            incoming=Money.of(6_000_000),
            outgoing=Money.of(5_000_000),
            top_payers=(
                Payer("7714023454", 'АО "МАШЗАВОД"', Money.of(1_800_000), Decimal("30.0")),
            ),
        ),
    ),
)


def _reviewed_application() -> Application:
    app = manual_review_application(reasons=("SECURED_VARIANT", "AMOUNT_OVER_5M"))
    snapshots = list(app.snapshots)
    turnover_at = next(i for i, s in enumerate(snapshots) if s.source is SourceType.TURNOVER)
    snapshots[turnover_at] = replace(snapshots[turnover_at], payload=to_payload(OWN_TURNOVER))
    app.record_snapshots(snapshots)
    app.record_checks(
        CheckStage.CHECKING,
        [
            CheckResult(
                code="COMPANY_INACTIVE",
                stage=CheckStage.CHECKING,
                expected="ACTIVE",
                actual="ACTIVE",
                outcome=CheckOutcome.PASSED,
            )
        ],
    )
    app.record_scoring(
        ScoringResult(
            total=100,
            risk_class=RiskClass.A,
            debt_load=0.15,
            factors=(FactorScore("BUSINESS_AGE", "Возраст бизнеса", "7 лет", 20),),
        )
    )
    app.decisions[-1] = replace(app.decisions[-1], rate=Decimal("23.0"))
    return app


def _history(app: Application) -> list[HistoryEntry]:
    return [
        HistoryEntry(
            transition=t,
            actor_role=Role.ANALYST if t.actor_id == ANALYST_ID else None,
            actor_name="Орлова Мария Игоревна" if t.actor_id == ANALYST_ID else "Система",
        )
        for t in app.history
    ]


async def _applicant() -> User:
    return await make_user(full_name="Кузнецов Игорь Валентинович")


async def test_analyst_report_has_all_sections() -> None:
    app = _reviewed_application()
    app.approve_by_analyst(
        Decimal("23.0"),
        actor_id=ANALYST_ID,
        comment="Залог покрывает лимит",
        now=NOW + timedelta(hours=1),
        offer_ttl=timedelta(days=7),
    )
    applicant = await _applicant()

    report = ReportBuilder().build(app, Role.ANALYST, applicant=applicant, history=_history(app))

    assert report.applicant is applicant
    assert [s.source for s in report.sources] == [s.source for s in app.snapshots]
    turnover_source = next(s for s in report.sources if s.source is SourceType.TURNOVER)
    assert turnover_source.status is FetchStatus.OK
    assert turnover_source.data["inn"] == "7704123450"
    assert [c.code for c in report.comparisons] == ["COMPANY_INACTIVE"]
    assert [(m.month, m.incoming, m.outgoing) for m in report.turnover] == [
        (date(2026, 8, 1), Money.of(4_000_000), Money.of(3_000_000)),
        (date(2026, 9, 1), Money.of(6_000_000), Money.of(5_000_000)),
    ]
    # Доля за весь период: 3 млн из 10 млн и 0,8 млн из 10 млн.
    assert [(p.inn, p.name, p.share_pct) for p in report.top_payers] == [
        ("7714023454", 'АО "МАШЗАВОД"', Decimal("30.0")),
        ("7705123452", 'ООО "ПРОМКОМПЛЕКТ"', Decimal("8.0")),
    ]
    assert report.scoring is app.scoring

    decision = report.decision
    assert decision is not None
    assert (decision.rule_code, decision.outcome) == (
        "MANUAL_REVIEW",
        DecisionOutcome.MANUAL_REVIEW,
    )
    assert decision.manual_reasons == ("SECURED_VARIANT", "AMOUNT_OVER_5M")
    assert decision.system_rate == Decimal("23.0")
    analyst = decision.analyst
    assert analyst is not None
    assert (analyst.actor_id, analyst.actor_name) == (ANALYST_ID, "Орлова Мария Игоревна")
    assert (analyst.rate, analyst.comment) == (Decimal("23.0"), "Залог покрывает лимит")

    assert [e.transition.to_status for e in report.history][-1] == "APPROVED"
    assert report.contract is None


async def test_report_before_analyst_decision_has_no_analyst_part() -> None:
    app = _reviewed_application()

    report = ReportBuilder().build(
        app, Role.ANALYST, applicant=await _applicant(), history=_history(app)
    )

    assert report.decision is not None
    assert report.decision.analyst is None


async def test_turnover_comes_from_statement_without_own_account() -> None:
    app = manual_review_application()
    statement = make_statement(
        date_from=date(2026, 8, 1), date_to=date(2026, 9, 30), monthly_incoming="1000000"
    )
    spending = StatementOperation(
        date=date(2026, 9, 15),
        amount=Money.of(400_000),
        direction=Direction.OUTGOING,
        counterparty_inn="7700000001",
    )
    app.statements.append(replace(statement, operations=(*statement.operations, spending)))

    report = ReportBuilder().build(
        app, Role.ANALYST, applicant=await _applicant(), history=_history(app)
    )

    assert [(m.month, m.incoming, m.outgoing) for m in report.turnover] == [
        (date(2026, 8, 1), Money.of(1_000_000), Money.zero()),
        (date(2026, 9, 1), Money.of(1_000_000), Money.of(400_000)),
    ]
    assert [(p.inn, p.name, p.share_pct) for p in report.top_payers] == [
        ("7702987655", "", Decimal("100.0"))
    ]


async def test_client_report_hides_internal_sections() -> None:
    app = _reviewed_application()

    report = ReportBuilder().build(
        app, Role.CLIENT, applicant=await _applicant(), history=_history(app)
    )

    assert (report.sources, report.comparisons, report.turnover, report.top_payers) == (
        (),
        (),
        (),
        (),
    )
    assert report.decision is not None
    assert report.scoring is app.scoring


def test_turnover_survives_payload_round_trip() -> None:
    assert turnover_from_payload(to_payload(OWN_TURNOVER)) == OWN_TURNOVER


def test_payload_of_scalar_answer_is_wrapped() -> None:
    assert to_payload(None) == {"value": None}
    assert to_payload(True) == {"value": True}
    assert turnover_from_payload(to_payload(None)) is None
