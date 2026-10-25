"""Решение для клиента: антифрод-факторы скрыты, причины отказа по классу D объяснены моделью."""

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


def test_antifraud_factors_are_hidden_from_client() -> None:
    out = ClientDecisionOut.from_view(_view(("CREDIT_HISTORY",)))

    assert out.scoring is not None
    assert [f.code for f in out.scoring.factors] == ["CREDIT_HISTORY", "DEBT_LOAD"]
    assert [f.label for f in out.key_factors] == ["Кредитная история", "Долговая нагрузка"]


def test_factor_reasons_use_model_texts() -> None:
    out = ClientDecisionOut.from_view(_view(("CREDIT_HISTORY", "DEBT_LOAD", "RISK_CLASS_D")))

    # Без пояснения берётся подпись фактора, не фактор — общий словарь причин.
    assert out.reasons == ["Были просрочки", "Долговая нагрузка", "Высокий кредитный риск"]
