from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from enum import Enum

from .calculator import calculate_score
from .config import DEFAULT_CONFIG, ScoringConfig
from .models import RiskClass, ScoringInput, ScoringResult


class Decision(str, Enum):
    APPROVED = "approved"
    COUNTER_OFFER = "counter_offer"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"


class ProductVariant:
    __slots__ = (
        "min_amount", "max_amount", "min_term_months", "max_term_months",
        "min_rate", "max_rate", "revenue_limit_pct",
    )

    def __init__(
        self,
        *,
        min_amount: Decimal,
        max_amount: Decimal,
        min_term_months: int,
        max_term_months: int,
        min_rate: Decimal,
        max_rate: Decimal,
        revenue_limit_pct: Decimal,
    ):
        self.min_amount = min_amount
        self.max_amount = max_amount
        self.min_term_months = min_term_months
        self.max_term_months = max_term_months
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.revenue_limit_pct = revenue_limit_pct


class CounterOfferOption:
    __slots__ = ("amount", "term_months", "rate", "with_collateral", "risk_class")

    def __init__(
        self,
        *,
        amount: Decimal,
        term_months: int,
        rate: Decimal,
        with_collateral: bool,
        risk_class: RiskClass,
    ):
        self.amount = amount
        self.term_months = term_months
        self.rate = rate
        self.with_collateral = with_collateral
        self.risk_class = risk_class


class DecisionResult:
    __slots__ = ("decision", "rate", "options", "reasons")

    def __init__(
        self,
        *,
        decision: Decision,
        rate: Decimal | None = None,
        options: list[CounterOfferOption] | None = None,
        reasons: list[str] | None = None,
    ):
        self.decision = decision
        self.rate = rate
        self.options = options
        self.reasons = reasons


AMOUNT_STEP = Decimal("100000")


def make_decision(
    scoring_result: ScoringResult,
    scoring_input: ScoringInput,
    variant: ProductVariant,
    collateral_variant: ProductVariant | None = None,
    manual_review_reasons: list[str] | None = None,
    scoring_config: ScoringConfig = DEFAULT_CONFIG,
) -> DecisionResult:
    reasons = manual_review_reasons or []
    risk_class = scoring_result.risk_class
    annual_revenue = scoring_input.monthly_revenue * 12
    revenue_limit = _round_amount(annual_revenue * variant.revenue_limit_pct)

    # Правило 1: есть поводы для ручной проверки
    if reasons:
        return DecisionResult(
            decision=Decision.MANUAL_REVIEW,
            rate=_rate_for_class(risk_class, variant),
            reasons=reasons,
        )

    # Правило 2: класс D → отказ
    if risk_class == RiskClass.D:
        return DecisionResult(
            decision=Decision.REJECTED,
            reasons=[
                f.explanation
                for f in scoring_result.factors
                if f.points < 0 and f.client_visible
            ],
        )

    # Правило 3: класс A/B, сумма больше лимита от выручки
    if risk_class in (RiskClass.A, RiskClass.B):
        if scoring_input.requested_amount > revenue_limit:
            return DecisionResult(
                decision=Decision.COUNTER_OFFER,
                options=_over_limit_options(
                    scoring_input, variant, collateral_variant,
                    annual_revenue, risk_class,
                ),
            )

        # Правила 4–5: класс A или B → одобрено
        return DecisionResult(
            decision=Decision.APPROVED,
            rate=_rate_for_class(risk_class, variant),
        )

    # Правило 6: класс C → контрпредложение с подбором условий для B
    return _handle_class_c(
        scoring_result, scoring_input, variant,
        collateral_variant, scoring_config,
    )


def _rate_for_class(risk_class: RiskClass, variant: ProductVariant) -> Decimal:
    if risk_class == RiskClass.A:
        return variant.min_rate
    if risk_class == RiskClass.B:
        return (variant.min_rate + variant.max_rate) / 2
    return variant.max_rate


def _round_amount(amount: Decimal) -> Decimal:
    return (amount // AMOUNT_STEP) * AMOUNT_STEP


def _over_limit_options(
    inp: ScoringInput,
    variant: ProductVariant,
    collateral_variant: ProductVariant | None,
    annual_revenue: Decimal,
    risk_class: RiskClass,
) -> list[CounterOfferOption]:
    options: list[CounterOfferOption] = []

    limit = _round_amount(annual_revenue * variant.revenue_limit_pct)
    if limit >= variant.min_amount:
        options.append(CounterOfferOption(
            amount=min(limit, variant.max_amount),
            term_months=inp.requested_term_months,
            rate=_rate_for_class(risk_class, variant),
            with_collateral=False,
            risk_class=risk_class,
        ))

    if collateral_variant is not None:
        col_limit = _round_amount(
            annual_revenue * collateral_variant.revenue_limit_pct,
        )
        if (
            inp.requested_amount <= col_limit
            and inp.requested_amount <= collateral_variant.max_amount
        ):
            options.append(CounterOfferOption(
                amount=inp.requested_amount,
                term_months=inp.requested_term_months,
                rate=_rate_for_class(risk_class, collateral_variant),
                with_collateral=True,
                risk_class=risk_class,
            ))

    return options


def _handle_class_c(
    scoring_result: ScoringResult,
    scoring_input: ScoringInput,
    variant: ProductVariant,
    collateral_variant: ProductVariant | None,
    scoring_config: ScoringConfig,
) -> DecisionResult:
    options: list[CounterOfferOption] = []

    best_amount = _find_max_amount_for_class_b(
        scoring_input, variant, scoring_config,
    )
    if best_amount is not None:
        options.append(CounterOfferOption(
            amount=best_amount,
            term_months=scoring_input.requested_term_months,
            rate=_rate_for_class(RiskClass.B, variant),
            with_collateral=False,
            risk_class=RiskClass.B,
        ))

    best_term = _find_min_term_for_class_b(
        scoring_input, variant, scoring_config,
    )
    if best_term is not None:
        options.append(CounterOfferOption(
            amount=scoring_input.requested_amount,
            term_months=best_term,
            rate=_rate_for_class(RiskClass.B, variant),
            with_collateral=False,
            risk_class=RiskClass.B,
        ))

    if options:
        return DecisionResult(
            decision=Decision.COUNTER_OFFER,
            options=options,
        )

    return DecisionResult(
        decision=Decision.MANUAL_REVIEW,
        rate=_rate_for_class(RiskClass.C, variant),
        reasons=["Не удалось подобрать условия для автоматического одобрения"],
    )


def _find_max_amount_for_class_b(
    inp: ScoringInput,
    variant: ProductVariant,
    config: ScoringConfig,
) -> Decimal | None:
    low = int(variant.min_amount)
    high = int(inp.requested_amount)
    result: int | None = None

    while low <= high:
        mid = (low + high) // 2
        modified = replace(inp, requested_amount=Decimal(mid))
        score = calculate_score(modified, config)
        if score.risk_class in (RiskClass.A, RiskClass.B):
            result = mid
            low = mid + 1
        else:
            high = mid - 1

    if result is None:
        return None

    rounded = _round_amount(Decimal(result))
    if rounded < variant.min_amount:
        return None

    # Проверяем после округления
    verify = calculate_score(replace(inp, requested_amount=rounded), config)
    if verify.risk_class not in (RiskClass.A, RiskClass.B):
        return None

    return rounded


def _find_min_term_for_class_b(
    inp: ScoringInput,
    variant: ProductVariant,
    config: ScoringConfig,
) -> int | None:
    for term in range(inp.requested_term_months, variant.max_term_months + 1):
        modified = replace(inp, requested_term_months=term)
        score = calculate_score(modified, config)
        if score.risk_class in (RiskClass.A, RiskClass.B):
            return term
    return None
