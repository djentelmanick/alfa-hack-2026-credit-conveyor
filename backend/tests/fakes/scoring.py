from collections.abc import Mapping

from app.domain.checks import PipelineContext
from app.domain.enums import RiskClass
from app.domain.scoring import FactorScore, ScoringModel, ScoringResult
from tests.fakes.scenarios import IVANOVA, KHLEBNY_DVOR, KOLOS, PETROVYKH, SOKOLOV, TECHPROM


def _factors(*points: int) -> tuple[FactorScore, ...]:
    codes = (
        ("BUSINESS_AGE", "Возраст бизнеса"),
        ("DEBT_LOAD", "Долговая нагрузка"),
        ("AMOUNT_TO_REVENUE", "Сумма заявки к годовой выручке"),
        ("CREDIT_HISTORY", "Кредитная история"),
        ("CLOSED_LOANS", "Закрытые кредиты без просрочек"),
        ("INDUSTRY_RISK", "Отраслевой риск"),
    )
    return tuple(
        FactorScore(code=code, label=label, value=f"{p:+d}", points=p)
        for (code, label), p in zip(codes, points, strict=True)
    )


# BACKEND_SPEC раздел 7 и SCOPE раздел 12: баллы по шести факторам, класс и нагрузка.
# Ромашки здесь нет: она отсеивается стоп-фактором, и скоринг для неё вызываться не должен.
SCOPE_RESULTS: Mapping[str, ScoringResult] = {
    KOLOS: ScoringResult(
        total=90, risk_class=RiskClass.A, debt_load=0.12, factors=_factors(20, 25, 15, 20, 10, 0)
    ),
    SOKOLOV: ScoringResult(
        total=55, risk_class=RiskClass.B, debt_load=0.34, factors=_factors(20, 10, 5, 20, 0, 0)
    ),
    IVANOVA: ScoringResult(
        total=50, risk_class=RiskClass.B, debt_load=0.13, factors=_factors(10, 25, 15, 0, 0, 0)
    ),
    # Компании представителей скопированы с «Колоса» и получают его результат.
    KHLEBNY_DVOR: ScoringResult(
        total=90, risk_class=RiskClass.A, debt_load=0.12, factors=_factors(20, 25, 15, 20, 10, 0)
    ),
    PETROVYKH: ScoringResult(
        total=90, risk_class=RiskClass.A, debt_load=0.12, factors=_factors(20, 25, 15, 20, 10, 0)
    ),
    TECHPROM: ScoringResult(
        total=100, risk_class=RiskClass.A, debt_load=0.15, factors=_factors(20, 25, 15, 20, 10, 10)
    ),
}


class FixedScoringEngine(ScoringModel):
    """Подмена модели скоринга, пока её нет: отдаёт заранее заданный результат по ИНН."""

    def __init__(self, results_by_inn: Mapping[str, ScoringResult] = SCOPE_RESULTS) -> None:
        self._results = dict(results_by_inn)
        self.calls: list[str] = []

    def score(self, ctx: PipelineContext) -> ScoringResult:
        inn = ctx.application.inn
        self.calls.append(inn)
        try:
            return self._results[inn]
        except KeyError:
            raise LookupError(f"Для ИНН {inn} не задан результат скоринга") from None
