"""Поводы для ручной проверки SCOPE раздел 4, этап 3: не отказ, но и не автоматическое одобрение."""

from abc import ABC, abstractmethod
from typing import ClassVar

from app.domain.checks.context import PipelineContext
from app.domain.dates import full_months_between
from app.domain.enums import KycLevel, Variant
from app.domain.scoring.result import ScoringResult
from app.domain.value_objects import Money


class ManualReviewTrigger(ABC):
    reason_code: ClassVar[str]

    @abstractmethod
    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool: ...


class SecuredVariantTrigger(ManualReviewTrigger):
    """Аналитик подтверждает описание и оценку залога (SCOPE, раздел 2)."""

    reason_code = "SECURED_VARIANT"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        return ctx.application.variant is Variant.SECURED


class UnverifiedStatementTrigger(ManualReviewTrigger):
    reason_code = "STATEMENT_UNVERIFIED"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        if ctx.statement is None:
            return False
        # Непроверенная выписка опаснее, чем лишняя ручная проверка.
        verification = ctx.statement_verification
        return verification is None or not verification.verified


class AmountOver5mTrigger(ManualReviewTrigger):
    THRESHOLD: ClassVar[Money] = Money.of(5_000_000)
    reason_code = "AMOUNT_OVER_5M"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        return ctx.application.amount > self.THRESHOLD


class RecentOwnerChangeTrigger(ManualReviewTrigger):
    MIN_MONTHS: ClassVar[int] = 6
    reason_code = "RECENT_OWNER_CHANGE"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        changed_at = ctx.company.director_changed_at
        return (
            changed_at is not None and full_months_between(changed_at, ctx.today) < self.MIN_MONTHS
        )


class UnreliableRegistryRecordTrigger(ManualReviewTrigger):
    reason_code = "UNRELIABLE_REGISTRY_RECORD"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        return bool(ctx.company.unreliable_records)


class CbrYellowLevelTrigger(ManualReviewTrigger):
    reason_code = "CBR_YELLOW_LEVEL"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        return ctx.cbr_level is KycLevel.YELLOW


class TaxDebtOver10kTrigger(ManualReviewTrigger):
    THRESHOLD: ClassVar[Money] = Money.of(10_000)
    reason_code = "TAX_DEBT_OVER_10K"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        return ctx.tax.tax_debt > self.THRESHOLD


class DirectorBailiffOver10kTrigger(ManualReviewTrigger):
    THRESHOLD: ClassVar[Money] = Money.of(10_000)
    reason_code = "DIRECTOR_BAILIFF_OVER_10K"

    def applies(self, ctx: PipelineContext, scoring: ScoringResult) -> bool:
        info = ctx.director_bailiff
        return info is not None and info.total_debt > self.THRESHOLD


def default_manual_review_triggers() -> list[ManualReviewTrigger]:
    """Порядок как в SCOPE: в таком порядке причины видит аналитик."""
    return [
        SecuredVariantTrigger(),
        UnverifiedStatementTrigger(),
        AmountOver5mTrigger(),
        RecentOwnerChangeTrigger(),
        UnreliableRegistryRecordTrigger(),
        CbrYellowLevelTrigger(),
        TaxDebtOver10kTrigger(),
        DirectorBailiffOver10kTrigger(),
    ]
