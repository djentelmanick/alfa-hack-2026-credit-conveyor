from dataclasses import dataclass

from app.domain.enums import RiskClass


@dataclass(frozen=True, slots=True)
class FactorScore:
    code: str
    label: str
    value: str
    points: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ScoringResult:
    total: int
    risk_class: RiskClass
    debt_load: float
    factors: tuple[FactorScore, ...] = ()
