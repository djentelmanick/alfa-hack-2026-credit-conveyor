from decimal import Decimal

from app.domain.enums import RiskClass
from app.domain.errors import FieldError, ValidationFailed
from app.domain.products.config import VariantConfig


class RatePolicy:
    """Ставка по классу (SCOPE, раздел 6): A нижняя граница, B середина, C верхняя граница."""

    def rate_for(self, variant: VariantConfig, risk_class: RiskClass) -> Decimal:
        match risk_class:
            case RiskClass.A:
                return variant.rate_min
            case RiskClass.B:
                return (variant.rate_min + variant.rate_max) / 2
            case RiskClass.C:
                return variant.rate_max
            case RiskClass.D:
                raise ValueError("Для класса D ставки нет: такие заявки не одобряются")

    def validate_manual(self, variant: VariantConfig, rate: Decimal) -> None:
        if not variant.rate_min <= rate <= variant.rate_max:
            raise ValidationFailed(
                [
                    FieldError(
                        "rate",
                        "RATE_OUT_OF_RANGE",
                        f"Ставка должна быть от {variant.rate_min}% до {variant.rate_max}%",
                    )
                ]
            )
