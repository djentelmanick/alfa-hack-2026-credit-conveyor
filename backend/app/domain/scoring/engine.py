from typing import TYPE_CHECKING

from app.domain.errors import ScoringNotImplemented
from app.domain.scoring.model import ScoringModel
from app.domain.scoring.result import ScoringResult

if TYPE_CHECKING:
    from app.domain.checks.context import PipelineContext


class ScoringEngine(ScoringModel):
    """Заглушка до появления модели (BACKEND_SPEC, раздел 7): контракт для неё описан там же."""

    def score(self, ctx: PipelineContext) -> ScoringResult:
        raise ScoringNotImplemented()
