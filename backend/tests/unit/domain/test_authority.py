from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.authority import REQUIRED_POWERS, PowerOfAttorney, check_power_of_attorney
from app.domain.enums import PoaKind, PoaPower
from app.domain.errors import FieldError
from app.domain.value_objects import CompanyProfile, is_valid_person_inn
from tests.fakes.auth import make_user
from tests.fakes.context import COMPANY_INN, DIRECTOR_INN, FOUNDER_INN, make_company

TODAY = date(2026, 10, 24)
REPRESENTATIVE = "771502345660"
FORMER_DIRECTOR = "771602345657"


@pytest.fixture
def company() -> CompanyProfile:
    return make_company()


@pytest.fixture
def valid() -> PowerOfAttorney:
    return PowerOfAttorney(
        kind=PoaKind.MCHD,
        principal_inn=COMPANY_INN,
        signed_by_inn=DIRECTOR_INN,
        signed_by_name="Петров Пётр Петрович",
        representative_inn=REPRESENTATIVE,
        valid_until=date(2027, 10, 31),
        revoked=False,
        powers=REQUIRED_POWERS,
    )


def _codes(errors: tuple[FieldError, ...]) -> list[str]:
    return [e.code for e in errors]


def _check(poa: PowerOfAttorney | None, company: CompanyProfile) -> tuple[FieldError, ...]:
    return check_power_of_attorney(
        poa, company=company, representative_inn=REPRESENTATIVE, today=TODAY
    )


def test_valid_poa_has_no_errors(valid: PowerOfAttorney, company: CompanyProfile) -> None:
    assert _check(valid, company) == ()


def test_poa_is_valid_on_its_last_day(valid: PowerOfAttorney, company: CompanyProfile) -> None:
    assert _check(replace(valid, valid_until=TODAY), company) == ()


def test_poa_expires_the_day_after(valid: PowerOfAttorney, company: CompanyProfile) -> None:
    poa = replace(valid, valid_until=TODAY - timedelta(days=1))
    assert _codes(_check(poa, company)) == ["POA_EXPIRED"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"revoked": True}, "POA_REVOKED"),
        ({"principal_inn": "7704123450"}, "POA_WRONG_PRINCIPAL"),
        ({"signed_by_inn": FORMER_DIRECTOR}, "POA_NOT_SIGNED_BY_DIRECTOR"),
        ({"representative_inn": FOUNDER_INN}, "POA_WRONG_REPRESENTATIVE"),
        ({"powers": frozenset({PoaPower.LOAN_APPLICATION})}, "POA_MISSING_POWER"),
    ],
)
def test_each_violation_has_its_code(
    valid: PowerOfAttorney, company: CompanyProfile, change: dict[str, object], code: str
) -> None:
    assert _codes(_check(replace(valid, **change), company)) == [code]


def test_missing_poa_is_not_found(company: CompanyProfile) -> None:
    assert _codes(_check(None, company)) == ["POA_NOT_FOUND"]


def test_all_violations_are_reported_together(
    valid: PowerOfAttorney, company: CompanyProfile
) -> None:
    poa = replace(valid, revoked=True, valid_until=date(2026, 1, 1), powers=frozenset())
    assert _codes(_check(poa, company)) == ["POA_REVOKED", "POA_EXPIRED", "POA_MISSING_POWER"]


def test_missing_signing_power_is_named(valid: PowerOfAttorney, company: CompanyProfile) -> None:
    [error] = _check(replace(valid, powers=frozenset({PoaPower.LOAN_APPLICATION})), company)
    assert error.field == "authority.power_of_attorney"
    assert error.message == "В доверенности нет полномочия: подписание кредитного договора"


def test_person_inn_checksum() -> None:
    assert is_valid_person_inn("771401234587")
    assert not is_valid_person_inn("771401234588")
    assert not is_valid_person_inn("7714012340")
    assert not is_valid_person_inn("７７１４０１２３４５８７")


async def test_director_is_matched_by_inn_not_by_name(company: CompanyProfile) -> None:
    namesake = await make_user(full_name="Петров Пётр Петрович", inn=FOUNDER_INN)
    director = await make_user(full_name="Другое Имя", inn=DIRECTOR_INN)
    assert company.is_director(director)
    assert not company.is_director(namesake)


async def test_user_without_inn_is_nobody(company: CompanyProfile) -> None:
    user = await make_user(full_name="Без Инн")
    assert user.inn is None
    assert not company.is_director(user)
    assert not company.is_founder(user)
