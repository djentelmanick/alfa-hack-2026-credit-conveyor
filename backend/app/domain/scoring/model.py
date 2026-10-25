from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.domain.scoring.result import ScoringResult

if TYPE_CHECKING:
    # Контекст ссылается на сущности, а те на ScoringResult: прямой импорт дал бы цикл.
    from app.domain.checks.context import PipelineContext


class ScoringModel(ABC):
    @abstractmethod
    def score(self, ctx: PipelineContext) -> ScoringResult: ...
