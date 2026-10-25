from collections.abc import Sequence

from app.domain.checks.context import PipelineContext
from app.domain.checks.manual_review import ManualReviewTrigger
from app.domain.decision.rules import DecisionRule
from app.domain.entities import Decision
from app.domain.scoring.result import ScoringResult


class DecisionEngine:
    def __init__(
        self, rules: Sequence[DecisionRule], triggers: Sequence[ManualReviewTrigger]
    ) -> None:
        orders = [rule.order for rule in rules]
        if len(orders) != len(set(orders)):
            raise ValueError(f"У правил решения повторяется order: {sorted(orders)}")
        self._rules = sorted(rules, key=lambda rule: rule.order)
        self._triggers = tuple(triggers)

    def manual_review_reasons(self, ctx: PipelineContext, scoring: ScoringResult) -> list[str]:
        return [t.reason_code for t in self._triggers if t.applies(ctx, scoring)]

    def decide(self, ctx: PipelineContext, scoring: ScoringResult) -> Decision:
        reasons = self.manual_review_reasons(ctx, scoring)
        for rule in self._rules:
            if rule.matches(ctx, scoring, reasons):
                return rule.apply(ctx, scoring, reasons)
        raise LookupError(f"Ни одно правило решения не подошло для класса {scoring.risk_class}")
