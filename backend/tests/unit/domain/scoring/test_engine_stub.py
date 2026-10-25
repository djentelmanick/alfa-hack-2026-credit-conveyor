import pytest

from app.domain.errors import ScoringNotImplemented
from app.domain.scoring import ScoringEngine, ScoringModel
from tests.fakes.context import make_context


def test_scoring_engine_is_a_stub_until_the_model_is_ready() -> None:
    engine = ScoringEngine()
    assert isinstance(engine, ScoringModel)
    with pytest.raises(ScoringNotImplemented):
        engine.score(make_context())


def test_scoring_model_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ScoringModel()
