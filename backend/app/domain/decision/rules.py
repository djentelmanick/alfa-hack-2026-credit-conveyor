"""Правила кредитного решения SCOPE раздел 6: по order, первое сработавшее даёт решение."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from app.domain.checks.context import PipelineContext
from app.domain.decision.rate_policy import RatePolicy
from app.domain.entities import Decision, OfferOption
from app.domain.enums import DecisionOutcome, RiskClass, Variant
from app.domain.products import LoanProduct, ProductCatalog
from app.domain.products.config import VariantConfig
from app.domain.scoring.result import ScoringResult
from app.domain.value_objects import Money

NO_COUNTER_OFFER_AVAILABLE = "NO_COUNTER_OFFER_AVAILABLE"


class DecisionRule(ABC):
    order: ClassVar[int]
    code: ClassVar[str]

    def __init__(self, catalog: ProductCatalog, rates: RatePolicy) -> None:
        self._catalog = catalog
        self._rates = rates

    @abstractmethod
    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        """reasons это сработавшие поводы ручной проверки."""

    @abstractmethod
    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision: ...

    def _product(self, ctx: PipelineContext) -> LoanProduct:
        return self._catalog.get(ctx.application.product_code)

    def _terms(self, ctx: PipelineContext) -> VariantConfig:
        return self._product(ctx).variant(ctx.application.variant)

    def _decision(
        self,
        ctx: PipelineContext,
        outcome: DecisionOutcome,
        *,
        rate: Decimal | None,
        reasons: Sequence[str] = (),
        offers: Sequence[OfferOption] = (),
    ) -> Decision:
        return Decision(
            id=uuid4(),
            rule_code=self.code,
            outcome=outcome,
            rate=rate,
            reasons=tuple(reasons),
            offers=tuple(offers),
            decided_by=None,
            comment=None,
            decided_at=ctx.now,
        )

    def _offer(
        self,
        ctx: PipelineContext,
        scoring: ScoringResult,
        variant: Variant,
        amount: Money,
        term_months: int,
    ) -> OfferOption | None:
        """Вариант с теми же продуктом и классом; None, если сумма не проходит границы варианта."""
        product = self._product(ctx)
        if variant not in product.config.variants:
            return None
        terms = product.variant(variant)
        if not terms.min_amount <= amount <= terms.max_amount:
            return None
        return OfferOption(
            id=uuid4(),
            product_code=product.code,
            variant=variant,
            amount=amount,
            term_months=min(max(term_months, terms.term_min), terms.term_max),
            rate=self._rates.rate_for(terms, scoring.risk_class),
        )

    def _counter_offer(
        self, ctx: PipelineContext, offers: Sequence[OfferOption | None]
    ) -> Decision:
        available = [o for o in offers if o is not None]
        if not available:
            # Предложить нечего, а одобрять запрошенное правило не вправе: решает аналитик.
            return self._decision(
                ctx,
                DecisionOutcome.MANUAL_REVIEW,
                rate=None,
                reasons=(NO_COUNTER_OFFER_AVAILABLE,),
            )
        return self._decision(
            ctx, DecisionOutcome.COUNTER_OFFER, rate=available[0].rate, offers=available
        )


class ManualReviewRule(DecisionRule):
    order = 1
    code = "MANUAL_REVIEW"

    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        return bool(reasons)

    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision:
        # Ставка по классу это подсказка аналитику, он назначает итоговую в диапазоне варианта.
        rate = (
            None
            if scoring.risk_class is RiskClass.D
            else self._rates.rate_for(self._terms(ctx), scoring.risk_class)
        )
        return self._decision(ctx, DecisionOutcome.MANUAL_REVIEW, rate=rate, reasons=reasons)


class ClassDRejectRule(DecisionRule):
    order = 2
    code = "CLASS_D_REJECT"

    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        return scoring.risk_class is RiskClass.D

    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision:
        negative = sorted((f for f in scoring.factors if f.points < 0), key=lambda f: f.points)
        key_factors = tuple(f.code for f in negative) or ("RISK_CLASS_D",)
        return self._decision(ctx, DecisionOutcome.REJECTED, rate=None, reasons=key_factors)


class RevenueLimitCounterOfferRule(DecisionRule):
    order = 3
    code = "REVENUE_LIMIT_COUNTER_OFFER"

    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        if scoring.risk_class not in (RiskClass.A, RiskClass.B):
            return False
        limit = self._product(ctx).revenue_limit(ctx.application.variant, ctx.annual_revenue())
        return ctx.application.amount > limit

    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision:
        product = self._product(ctx)
        revenue = ctx.annual_revenue()
        application = ctx.application
        offers = [
            self._offer(
                ctx,
                scoring,
                Variant.UNSECURED,
                product.revenue_limit(Variant.UNSECURED, revenue),
                application.term_months,
            )
        ]
        if (
            Variant.SECURED in product.config.variants
            and application.amount <= product.revenue_limit(Variant.SECURED, revenue)
        ):
            offers.append(
                self._offer(
                    ctx, scoring, Variant.SECURED, application.amount, application.term_months
                )
            )
        return self._counter_offer(ctx, offers)


class _ApproveRule(DecisionRule):
    risk_class: ClassVar[RiskClass]

    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        return scoring.risk_class is self.risk_class

    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision:
        rate = self._rates.rate_for(self._terms(ctx), scoring.risk_class)
        return self._decision(ctx, DecisionOutcome.APPROVED, rate=rate)


class ClassAApproveRule(_ApproveRule):
    order = 4
    code = "CLASS_A_APPROVE"
    risk_class = RiskClass.A


class ClassBApproveRule(_ApproveRule):
    order = 5
    code = "CLASS_B_APPROVE"
    risk_class = RiskClass.B


class ClassCCounterOfferRule(DecisionRule):
    """Та же сумма на максимальный срок варианта или та же сумма с залогом."""

    order = 6
    code = "CLASS_C_COUNTER_OFFER"

    def matches(self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]) -> bool:
        return scoring.risk_class is RiskClass.C

    def apply(
        self, ctx: PipelineContext, scoring: ScoringResult, reasons: Sequence[str]
    ) -> Decision:
        application = ctx.application
        terms = self._terms(ctx)
        offers: list[OfferOption | None] = []
        if application.term_months < terms.term_max:
            offers.append(
                self._offer(ctx, scoring, application.variant, application.amount, terms.term_max)
            )
        if application.variant is Variant.UNSECURED:
            offers.append(
                self._offer(
                    ctx, scoring, Variant.SECURED, application.amount, application.term_months
                )
            )
        return self._counter_offer(ctx, offers)


def default_decision_rules(catalog: ProductCatalog, rates: RatePolicy) -> list[DecisionRule]:
    return [
        ManualReviewRule(catalog, rates),
        ClassDRejectRule(catalog, rates),
        RevenueLimitCounterOfferRule(catalog, rates),
        ClassAApproveRule(catalog, rates),
        ClassBApproveRule(catalog, rates),
        ClassCCounterOfferRule(catalog, rates),
    ]
