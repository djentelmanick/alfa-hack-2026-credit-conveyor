"""Режим «сломаться» для демо (SCOPE, раздел 4)."""

import pytest

from app.domain.entities import Consent
from app.domain.enums import SourceType
from app.domain.errors import SourceUnavailable
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockRosfinAdapter,
    MockSignatureAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from tests.unit.infra.conftest import IVANOVA, KOLOS, TECHPROM


async def test_broken_inn_fails_and_others_work(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    adapter = MockEgrulAdapter(fixtures, failures)
    failures.set(SourceType.EGRUL, KOLOS, enabled=True)

    with pytest.raises(SourceUnavailable):
        await adapter.find_by_inn(KOLOS)
    assert (await adapter.find_by_inn(TECHPROM)).inn == TECHPROM


async def test_breaks_only_the_chosen_source(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    failures.set(SourceType.CBR_KYC, KOLOS, enabled=True)

    assert (await MockEgrulAdapter(fixtures, failures).find_by_inn(KOLOS)).inn == KOLOS
    with pytest.raises(SourceUnavailable):
        await MockCbrAdapter(fixtures, failures).risk_level(KOLOS)


async def test_fails_on_every_call_until_disabled(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    adapter = MockRosfinAdapter(fixtures, failures)
    failures.set(SourceType.ROSFIN, KOLOS, enabled=True)
    for _ in range(3):
        with pytest.raises(SourceUnavailable):
            await adapter.is_listed(KOLOS, [])

    failures.set(SourceType.ROSFIN, KOLOS, enabled=False)

    assert not await adapter.is_listed(KOLOS, [])
    assert failures.failing_inns(SourceType.ROSFIN) == frozenset()


async def test_fns_and_gir_bo_break_separately(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    adapter = MockFnsAdapter(fixtures, failures)
    failures.set(SourceType.GIR_BO, KOLOS, enabled=True)

    with pytest.raises(SourceUnavailable):
        await adapter.annual_revenue(KOLOS, 2025)
    assert (await adapter.tax_info(KOLOS)).inn == KOLOS


async def test_broken_company_breaks_bki_of_its_director_and_founders(
    fixtures: SyntheticData, failures: MockFailures, consent: Consent
) -> None:
    adapter = MockBkiAdapter(fixtures, failures)
    failures.set(SourceType.BKI, KOLOS, enabled=True)

    for subject in ("771401234587", "771402345674"):
        with pytest.raises(SourceUnavailable):
            await adapter.report(subject, consent)
    assert (await adapter.report("770623456765", consent)).subject_inn == "770623456765"


async def test_broken_company_breaks_fssp_of_its_director(
    fixtures: SyntheticData, failures: MockFailures
) -> None:
    failures.set(SourceType.FSSP, TECHPROM, enabled=True)

    with pytest.raises(SourceUnavailable):
        await MockFsspAdapter(fixtures, failures).open_proceedings("770623456765")


async def test_turnover_breaks(fixtures: SyntheticData, failures: MockFailures) -> None:
    failures.set(SourceType.TURNOVER, KOLOS, enabled=True)

    with pytest.raises(SourceUnavailable):
        await MockTurnoverAdapter(fixtures, failures).monthly_turnover(KOLOS, 12)


async def test_signature_breaks_by_statement_owner(
    fixtures: SyntheticData, failures: MockFailures, statement_12m: bytes
) -> None:
    failures.set(SourceType.BANK_SIGNATURE, IVANOVA, enabled=True)

    with pytest.raises(SourceUnavailable):
        await MockSignatureAdapter(fixtures, failures).verify(statement_12m)


def test_clear_resets_all_sources(failures: MockFailures) -> None:
    failures.set(SourceType.EGRUL, KOLOS, enabled=True)
    failures.set(SourceType.BKI, TECHPROM, enabled=True)

    failures.clear()

    assert failures.failing_inns(SourceType.EGRUL) == frozenset()
    assert failures.failing_inns(SourceType.BKI) == frozenset()
