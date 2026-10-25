"""Повторы запросов к источникам (SCOPE, раздел 4)."""

import pytest

from app.domain.enums import KycLevel, SourceType
from app.domain.errors import NotFound, SourceUnavailable
from app.domain.ports import CbrKycPort
from app.infra.mocks import MockEgrulAdapter, MockFailures, SyntheticData
from app.infra.retrying import (
    RetryingCbrKyc,
    RetryingCompanyRegistry,
    RetryPolicy,
)
from tests.unit.infra.conftest import KOLOS, TECHPROM, RecordingSleep

POLICY = RetryPolicy(attempts=3, pause_ms=500)


class FlakyCbr(CbrKycPort):
    def __init__(self, failures_before_success: int) -> None:
        self.calls = 0
        self._failures_left = failures_before_success

    async def risk_level(self, inn: str) -> KycLevel:
        self.calls += 1
        if self._failures_left > 0:
            self._failures_left -= 1
            raise SourceUnavailable
        return KycLevel.GREEN


async def test_makes_exactly_n_attempts_then_raises() -> None:
    inner = FlakyCbr(failures_before_success=10)
    sleep = RecordingSleep()

    with pytest.raises(SourceUnavailable, match="попыток 3"):
        await RetryingCbrKyc(inner, POLICY, sleep=sleep).risk_level(KOLOS)

    assert inner.calls == 3
    assert sleep.calls == [0.5, 0.5]


async def test_succeeds_after_transient_failure() -> None:
    inner = FlakyCbr(failures_before_success=2)
    sleep = RecordingSleep()

    assert await RetryingCbrKyc(inner, POLICY, sleep=sleep).risk_level(KOLOS) is KycLevel.GREEN
    assert inner.calls == 3
    assert sleep.calls == [0.5, 0.5]


async def test_first_success_has_no_pause() -> None:
    inner = FlakyCbr(failures_before_success=0)
    sleep = RecordingSleep()

    await RetryingCbrKyc(inner, POLICY, sleep=sleep).risk_level(KOLOS)

    assert inner.calls == 1
    assert sleep.calls == []


async def test_single_attempt_policy() -> None:
    inner = FlakyCbr(failures_before_success=1)

    with pytest.raises(SourceUnavailable):
        await RetryingCbrKyc(inner, RetryPolicy(attempts=1, pause_ms=0)).risk_level(KOLOS)
    assert inner.calls == 1


async def test_not_found_is_not_retried(fixtures: SyntheticData, failures: MockFailures) -> None:
    sleep = RecordingSleep()
    gateway = RetryingCompanyRegistry(MockEgrulAdapter(fixtures, failures), POLICY, sleep=sleep)

    with pytest.raises(NotFound):
        await gateway.find_by_inn("7700000001")
    assert sleep.calls == []


async def test_broken_mock_exhausts_all_attempts(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    failures.set(SourceType.EGRUL, KOLOS, enabled=True)
    sleep = RecordingSleep()
    gateway = RetryingCompanyRegistry(MockEgrulAdapter(fixtures, failures), POLICY, sleep=sleep)

    with pytest.raises(SourceUnavailable):
        await gateway.find_by_inn(KOLOS)
    assert len(sleep.calls) == POLICY.attempts - 1
    assert (await gateway.find_by_inn(TECHPROM)).inn == TECHPROM


@pytest.mark.parametrize(("attempts", "pause_ms"), [(0, 100), (3, -1)])
def test_policy_rejects_invalid_values(attempts: int, pause_ms: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 текст ошибки здесь не важен
        RetryPolicy(attempts=attempts, pause_ms=pause_ms)
