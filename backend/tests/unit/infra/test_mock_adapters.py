from datetime import date

import pytest

from app.domain.entities import Consent
from app.domain.enums import CompanyKind, CompanyStatus, KycLevel, SignatureStatus
from app.domain.errors import NotFound
from app.domain.value_objects import Founder, Money
from app.infra.mocks import (
    MockBkiAdapter,
    MockCbrAdapter,
    MockEgrulAdapter,
    MockFailures,
    MockFnsAdapter,
    MockFsspAdapter,
    MockPassportAdapter,
    MockRosfinAdapter,
    MockSignatureAdapter,
    MockTurnoverAdapter,
    SyntheticData,
)
from tests.fakes.scenarios import KHLEBNY_DVOR, PETROVYKH
from tests.unit.infra.conftest import (
    ALL_INNS,
    IVANOVA,
    KOLOS,
    ROMASHKA,
    SOKOLOV,
    TECHPROM,
    RecordingSleep,
)

UNKNOWN_INN = "7700000001"


class TestSyntheticData:
    def test_loads_scope_and_representative_companies(self, fixtures: SyntheticData) -> None:
        assert set(fixtures.companies) == {*ALL_INNS, KHLEBNY_DVOR, PETROVYKH}

    def test_person_is_linked_to_their_company(self, fixtures: SyntheticData) -> None:
        director = fixtures.companies[TECHPROM].egrul.director
        assert director is not None
        assert fixtures.companies_of(director.inn) == {TECHPROM}
        assert fixtures.companies_of(UNKNOWN_INN) == frozenset()

    def test_rejects_inconsistent_bki_summary(self, fixtures: SyntheticData) -> None:
        company = fixtures.companies[KOLOS]
        report = company.bki[0]
        broken = report.model_copy(
            update={"summary": report.summary.model_copy(update={"total_monthly_payments": 1})}
        )
        with pytest.raises(ValueError, match="платежи БКИ"):
            SyntheticData(
                [company.model_copy(update={"bki": (broken,)})],
                fixtures.rosfin,
                fixtures.passports,
            )


class TestEgrul:
    async def test_llc_profile(self, fixtures: SyntheticData, failures: MockFailures) -> None:
        profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(TECHPROM)

        assert profile.kind is CompanyKind.LLC
        assert profile.name == 'ООО "ТЕХПРОМ"'
        assert profile.ogrn == "1197700987659"
        assert profile.registered_at == date(2019, 9, 10)
        assert profile.director == "КУЗНЕЦОВ ИГОРЬ ВАЛЕНТИНОВИЧ"
        assert profile.director_inn == "770623456765"
        assert [f.share_pct for f in profile.founders] == [55, 30, 15]
        assert profile.founders[1] == Founder("ЛЕБЕДЕВ ПАВЕЛ ОЛЕГОВИЧ", "770734567870", 30)
        # Последняя смена состава: учредитель с долей 15% вошёл 01.06.2022.
        assert profile.director_changed_at == date(2022, 6, 1)

    async def test_llc_without_changes_since_registration(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(KOLOS)
        assert profile.director_changed_at is None

    async def test_individual_entrepreneur_is_own_director(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(SOKOLOV)

        assert profile.kind is CompanyKind.IE
        assert profile.name == "ИП СОКОЛОВ ДМИТРИЙ АНДРЕЕВИЧ"
        assert profile.ogrn == "322774600123452"
        assert profile.director_inn == SOKOLOV
        assert profile.founders == ()

    async def test_liquidated_company(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        profile = await MockEgrulAdapter(fixtures, failures).find_by_inn(ROMASHKA)
        assert profile.status is CompanyStatus.LIQUIDATED

    async def test_unknown_inn_is_not_found(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        with pytest.raises(NotFound):
            await MockEgrulAdapter(fixtures, failures).find_by_inn(UNKNOWN_INN)

    async def test_waits_pipeline_step_delay(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        sleep = RecordingSleep()
        adapter = MockEgrulAdapter(fixtures, failures, delay_ms=800, sleep=sleep)

        await adapter.find_by_inn(KOLOS)

        assert sleep.calls == [0.8]


class TestBki:
    @pytest.mark.parametrize(
        ("inn", "payments", "has_history", "closed"),
        [
            (KOLOS, 50_000, True, True),
            (ROMASHKA, 0, True, True),
            (SOKOLOV, 100_000, True, False),
            (IVANOVA, 0, False, False),
            (TECHPROM, 400_000, True, True),
        ],
    )
    async def test_company_reports(
        self,
        fixtures: SyntheticData,
        failures: MockFailures,
        consent: Consent,
        inn: str,
        payments: int,
        has_history: bool,
        closed: bool,
    ) -> None:
        report = await MockBkiAdapter(fixtures, failures).report(inn, consent)

        assert report.subject_inn == inn
        assert report.monthly_payments == Money.of(payments)
        assert report.has_history is has_history
        assert report.has_closed_loans_without_overdue is closed
        assert report.current_overdue_days == 0

    async def test_director_report_has_no_payments(
        self, fixtures: SyntheticData, failures: MockFailures, consent: Consent
    ) -> None:
        report = await MockBkiAdapter(fixtures, failures).report("771401234587", consent)

        assert report.has_history
        assert report.monthly_payments == Money.zero()

    async def test_inquiries_last_three_months(
        self, fixtures: SyntheticData, failures: MockFailures, consent: Consent
    ) -> None:
        report = await MockBkiAdapter(fixtures, failures).report(TECHPROM, consent)
        assert report.inquiries_last_3_months == 1

    async def test_unknown_subject_has_no_history(
        self, fixtures: SyntheticData, failures: MockFailures, consent: Consent
    ) -> None:
        report = await MockBkiAdapter(fixtures, failures).report(UNKNOWN_INN, consent)

        assert not report.has_history
        assert report.monthly_payments == Money.zero()


class TestTurnover:
    @pytest.mark.parametrize(
        ("inn", "annual"),
        [(KOLOS, 24_000_000), (ROMASHKA, 6_000_000), (SOKOLOV, 12_000_000), (TECHPROM, 60_000_000)],
    )
    async def test_annual_turnover(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str, annual: int
    ) -> None:
        turnover = await MockTurnoverAdapter(fixtures, failures).monthly_turnover(inn, 12)

        assert turnover is not None
        assert turnover.total() == Money.of(annual)
        assert turnover.months[0].month == date(2025, 10, 1)
        assert turnover.months[-1].month == date(2026, 9, 1)

    async def test_returns_last_months_only(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        turnover = await MockTurnoverAdapter(fixtures, failures).monthly_turnover(KOLOS, 3)

        assert turnover is not None
        assert [m.month for m in turnover.months] == [
            date(2026, 7, 1),
            date(2026, 8, 1),
            date(2026, 9, 1),
        ]

    @pytest.mark.parametrize("inn", [IVANOVA, UNKNOWN_INN])
    async def test_no_account_in_our_bank(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str
    ) -> None:
        assert await MockTurnoverAdapter(fixtures, failures).monthly_turnover(inn, 12) is None


class TestFns:
    @pytest.mark.parametrize("inn", ALL_INNS)
    async def test_tax_info_is_clean(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str
    ) -> None:
        info = await MockFnsAdapter(fixtures, failures).tax_info(inn)

        assert info.inn == inn
        assert not info.accounts_blocked
        assert not info.director_disqualified
        assert info.tax_debt == Money.zero()
        assert info.data_date == date(2026, 9, 1)

    async def test_tax_debt_takes_larger_of_two_sums(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        company = fixtures.companies[KOLOS]
        fns = company.fns_transparent.model_copy(
            update={"arrears_and_penalties": 12_000, "tax_debt_enforcement": 8_000}
        )
        data = SyntheticData(
            [company.model_copy(update={"fns_transparent": fns})],
            fixtures.rosfin,
            fixtures.passports,
        )

        info = await MockFnsAdapter(data, failures).tax_info(KOLOS)

        assert info.tax_debt == Money.of(12_000)

    @pytest.mark.parametrize(
        ("inn", "revenue"),
        [(KOLOS, 23_400_000), (ROMASHKA, 5_800_000), (TECHPROM, 58_700_000)],
    )
    async def test_gir_bo_revenue_in_rubles(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str, revenue: int
    ) -> None:
        assert await MockFnsAdapter(fixtures, failures).annual_revenue(inn, 2025) == Money.of(
            revenue
        )

    @pytest.mark.parametrize(("inn", "year"), [(SOKOLOV, 2025), (IVANOVA, 2025), (KOLOS, 2024)])
    async def test_no_gir_bo_for_entrepreneurs_and_missing_years(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str, year: int
    ) -> None:
        assert await MockFnsAdapter(fixtures, failures).annual_revenue(inn, year) is None

    async def test_mass_address(self, fixtures: SyntheticData, failures: MockFailures) -> None:
        company = fixtures.companies[KOLOS]
        fns = company.fns_transparent.model_copy(update={"mass_address": True})
        data = SyntheticData(
            [company.model_copy(update={"fns_transparent": fns})],
            fixtures.rosfin,
            fixtures.passports,
        )
        adapter = MockFnsAdapter(data, failures)

        assert await adapter.is_mass_address(company.egrul.address.lower())
        assert not await adapter.is_mass_address("Г.МОСКВА, УЛ. ДРУГАЯ, Д. 1")

    async def test_unknown_inn(self, fixtures: SyntheticData, failures: MockFailures) -> None:
        adapter = MockFnsAdapter(fixtures, failures)

        assert not await adapter.account_blocks(UNKNOWN_INN)
        with pytest.raises(NotFound):
            await adapter.tax_info(UNKNOWN_INN)


class TestFssp:
    @pytest.mark.parametrize("inn", [*ALL_INNS, "771401234587", UNKNOWN_INN])
    async def test_no_open_proceedings(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str
    ) -> None:
        info = await MockFsspAdapter(fixtures, failures).open_proceedings(inn)

        assert info.subject_inn == inn
        assert info.open_proceedings == 0
        assert info.total_debt == Money.zero()


class TestCbr:
    @pytest.mark.parametrize("inn", [*ALL_INNS, UNKNOWN_INN])
    async def test_green_level(
        self, fixtures: SyntheticData, failures: MockFailures, inn: str
    ) -> None:
        assert await MockCbrAdapter(fixtures, failures).risk_level(inn) is KycLevel.GREEN


class TestRosfin:
    async def test_scope_companies_are_not_listed(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        adapter = MockRosfinAdapter(fixtures, failures)
        for inn, company in fixtures.companies.items():
            persons = [f.full_name for f in company.egrul.founders]
            assert not await adapter.is_listed(inn, persons)

    async def test_listed_organization(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        assert await MockRosfinAdapter(fixtures, failures).is_listed("7709999995", [])

    async def test_listed_person_matches_case_insensitively(
        self, fixtures: SyntheticData, failures: MockFailures
    ) -> None:
        adapter = MockRosfinAdapter(fixtures, failures)
        assert await adapter.is_listed(KOLOS, ["Петров А.Н.", "тестовый  фигурант перечня"])


class TestSignature:
    async def test_signed_statement_is_valid(
        self, fixtures: SyntheticData, failures: MockFailures, statement_12m: bytes
    ) -> None:
        status = await MockSignatureAdapter(fixtures, failures).verify(statement_12m)
        assert status is SignatureStatus.VALID

    async def test_unsigned_statement(
        self, fixtures: SyntheticData, failures: MockFailures, statement_4m: bytes
    ) -> None:
        status = await MockSignatureAdapter(fixtures, failures).verify(statement_4m)
        assert status is SignatureStatus.ABSENT

    async def test_tampered_statement_is_invalid(
        self, fixtures: SyntheticData, failures: MockFailures, statement_12m: bytes
    ) -> None:
        original = "Сумма=42958.54".encode("cp1251")
        tampered = statement_12m.replace(original, "Сумма=92958.54".encode("cp1251"), 1)
        assert tampered != statement_12m

        status = await MockSignatureAdapter(fixtures, failures).verify(tampered)

        assert status is SignatureStatus.INVALID


class TestPassport:
    @pytest.mark.parametrize(
        ("series", "number", "valid"),
        [
            ("4510", "654321", True),
            ("4500", "000000", False),
            ("4509", "123456", False),
            ("451", "654321", False),
            ("4510", "65432a", False),
            ("４５１０", "654321", False),
        ],
    )
    async def test_validity(
        self,
        fixtures: SyntheticData,
        failures: MockFailures,
        series: str,
        number: str,
        valid: bool,
    ) -> None:
        assert await MockPassportAdapter(fixtures, failures).is_valid(series, number) is valid
