import pytest

from app.domain.scoring import ScoringModel


def test_scoring_model_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ScoringModel()
