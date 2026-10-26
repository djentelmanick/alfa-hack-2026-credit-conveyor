"""Решение для клиента: без баллов, причины отказа по классу D объяснены моделью."""

from datetime import UTC, datetime
from uuid import uuid4

from app.api.v1.schemas.application import ClientDecisionOut
from app.application.application_service import DecisionView
from app.domain.entities import Decision
from app.domain.enums import DecisionOutcome, RiskClass
from app.domain.scoring import FactorScore, ScoringResult

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def _view(reasons: tuple[str, ...]) -> DecisionView:
    scoring = ScoringResult(
        total=-20,
        risk_class=RiskClass.D,
        debt_load=0.55,
        factors=(
            FactorScore("CREDIT_HISTORY", "Кредитная история", "просрочки", -20, "Были просрочки"),
            FactorScore("DEBT_LOAD", "Долговая нагрузка", "55%", -15),
            FactorScore("MASS_ADDRESS", "Адрес", "да", -30, "Массовый адрес", client_visible=False),
        ),
    )
    decision = Decision(
        id=uuid4(),
        rule_code="CLASS_D_REJECT",
        outcome=DecisionOutcome.REJECTED,
        rate=None,
        reasons=reasons,
        decided_by=None,
        comment=None,
        decided_at=NOW,
    )
    return DecisionView(decision=decision, scoring=scoring, expires_at=None)


def test_client_gets_no_scores() -> None:
    out = ClientDecisionOut.from_view(_view(("CREDIT_HISTORY",))).model_dump()

    assert "scoring" not in out
    assert "key_factors" not in out
    assert "-20" not in str(out)


def test_factor_reasons_use_model_texts() -> None:
    out = ClientDecisionOut.from_view(_view(("CREDIT_HISTORY", "DEBT_LOAD", "RISK_CLASS_D")))

    # Без пояснения берётся подпись фактора, не фактор — общий словарь причин.
    assert out.reasons == ["Были просрочки", "Долговая нагрузка", "Высокий кредитный риск"]
