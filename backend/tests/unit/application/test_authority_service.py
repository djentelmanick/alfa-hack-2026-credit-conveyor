from datetime import date
from uuid import UUID

import pytest

from app.application.authority_service import AuthorityClaim, AuthorityService
from app.domain.authority import PoaRequest
from app.domain.entities import User
from app.domain.enums import ApplicantRole, AuthorityBasis, PoaKind, SourceType
from app.domain.errors import NotDirector, SourceUnavailable, ValidationFailed
from app.domain.value_objects import CompanyProfile
from app.infra.mocks import MockEgrulAdapter, MockFailures, SyntheticData
from tests.fakes.auth import FixedClock, make_authority_service, make_user
from tests.fakes.scenarios import KHLEBNY_DVOR, KOLOS, PETROVYKH, SOKOLOV, TECHPROM

GUID_BUH = UUID("5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e01")
GUID_LEBEDEV = UUID("5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e02")
GUID_REVOKED = UUID("5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e03")
GUID_EXPIRED = UUID("5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e04")
GUID_FORMER_DIRECTOR = UUID("5f0c2b9e-1d4a-4c6b-9a7e-2b8f3c1d0e05")
NOTARY_PETROVA = PoaRequest(
    kind=PoaKind.NOTARIAL,
    notarized_on=date(2026, 9, 15),
    register_number="77/123-н/77-2026-4-512",
    notary_name="Белова Ирина Александровна",
)


def _mchd(guid: UUID) -> PoaRequest:
    return PoaRequest(kind=PoaKind.MCHD, mchd_guid=guid)


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


@pytest.fixture
def failures() -> MockFailures:
    return MockFailures()


@pytest.fixture
def service(fixtures: SyntheticData, failures: MockFailures) -> AuthorityService:
    return make_authority_service(fixtures, failures, FixedClock())


async def _company(fixtures: SyntheticData, inn: str) -> CompanyProfile:
    return await MockEgrulAdapter(fixtures, MockFailures()).find_by_inn(inn)


async def _person(full_name: str) -> User:
    return await make_user(full_name=full_name)


async def test_director_passes_as_director_whatever_role_and_poa(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    morozov = await _person("Морозов Сергей Петрович")
    claim = AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER, poa=_mchd(GUID_LEBEDEV))

    authority = await service.resolve(morozov, await _company(fixtures, KHLEBNY_DVOR), claim)

    assert (authority.basis, authority.role, authority.poa) == (
        AuthorityBasis.DIRECTOR,
        ApplicantRole.DIRECTOR,
        None,
    )


async def test_sole_trader_passes_as_sole_trader(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    sokolov = await _person("Соколов Дмитрий Андреевич")
    authority = await service.resolve(sokolov, await _company(fixtures, SOKOLOV), None)
    assert authority.basis is AuthorityBasis.SOLE_TRADER


async def test_accountant_with_mchd_is_representative(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER, poa=_mchd(GUID_BUH))

    authority = await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)

    assert authority.basis is AuthorityBasis.POWER_OF_ATTORNEY
    assert authority.role is ApplicantRole.FINANCE_OFFICER
    assert authority.representative_inn == "771502345660"
    assert authority.registered_founder is None


async def test_owner_with_notarial_poa_is_registered_founder(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    petrova = await _person("Петрова Мария Сергеевна")
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=NOTARY_PETROVA)

    authority = await service.resolve(petrova, await _company(fixtures, PETROVYKH), claim)

    assert authority.basis is AuthorityBasis.POWER_OF_ATTORNEY
    assert authority.registered_founder is True


async def test_owner_outside_founders_is_flagged_not_refused(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=_mchd(GUID_BUH))

    authority = await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)

    assert authority.registered_founder is False


@pytest.mark.parametrize("claim", [None, AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER)])
async def test_not_director_without_poa_is_forbidden(
    service: AuthorityService, fixtures: SyntheticData, claim: AuthorityClaim | None
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    with pytest.raises(NotDirector):
        await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)


async def test_poa_does_not_open_a_sole_trader(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER, poa=_mchd(GUID_BUH))
    with pytest.raises(NotDirector):
        await service.resolve(buh, await _company(fixtures, SOKOLOV), claim)


async def test_claiming_director_role_when_not_director(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.DIRECTOR, poa=_mchd(GUID_BUH))

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)

    [error] = exc.value.details
    assert (error.field, error.code) == ("authority.role", "NOT_DIRECTOR_BY_EGRUL")
    assert "МОРОЗОВ СЕРГЕЙ ПЕТРОВИЧ" in error.message


@pytest.mark.parametrize(
    ("guid", "code"),
    [
        (GUID_REVOKED, "POA_REVOKED"),
        (GUID_EXPIRED, "POA_EXPIRED"),
        (GUID_FORMER_DIRECTOR, "POA_NOT_SIGNED_BY_DIRECTOR"),
        (UUID("00000000-0000-0000-0000-000000000000"), "POA_NOT_FOUND"),
    ],
)
async def test_bad_mchd_is_rejected(
    service: AuthorityService, fixtures: SyntheticData, guid: UUID, code: str
) -> None:
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER, poa=_mchd(guid))

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)

    assert [e.code for e in exc.value.details] == [code]


async def test_someone_elses_mchd_is_not_found(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    # Реестр ФНС находит МЧД только вместе с ИНН представителя: чужая МЧД выглядит несуществующей.
    petrova = await _person("Петрова Мария Сергеевна")
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=_mchd(GUID_BUH))

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(petrova, await _company(fixtures, KHLEBNY_DVOR), claim)

    assert [e.code for e in exc.value.details] == ["POA_NOT_FOUND"]


async def test_lebedev_mchd_lacks_signing_power(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    lebedev = await _person("Лебедев Павел Олегович")
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=_mchd(GUID_LEBEDEV))

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(lebedev, await _company(fixtures, TECHPROM), claim)

    assert [e.code for e in exc.value.details] == ["POA_MISSING_POWER"]


async def test_cancelled_notarial_poa_is_revoked(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    petrova = await _person("Петрова Мария Сергеевна")
    cancelled = PoaRequest(
        kind=PoaKind.NOTARIAL,
        notarized_on=date(2026, 8, 1),
        register_number="77/123-н/77-2026-3-207",
        notary_name="  белова ирина   александровна ",
    )
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=cancelled)

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(petrova, await _company(fixtures, PETROVYKH), claim)

    assert [e.code for e in exc.value.details] == ["POA_REVOKED"]


async def test_notarial_poa_of_another_company_is_wrong_principal(
    service: AuthorityService, fixtures: SyntheticData
) -> None:
    petrova = await _person("Петрова Мария Сергеевна")
    claim = AuthorityClaim(role=ApplicantRole.OWNER, poa=NOTARY_PETROVA)

    with pytest.raises(ValidationFailed) as exc:
        await service.resolve(petrova, await _company(fixtures, KOLOS), claim)

    assert [e.code for e in exc.value.details] == ["POA_WRONG_PRINCIPAL"]


async def test_broken_registry_is_unavailable(
    service: AuthorityService, fixtures: SyntheticData, failures: MockFailures
) -> None:
    await failures.set(SourceType.MCHD, KHLEBNY_DVOR, enabled=True)
    buh = await _person("Васильева Анна Игоревна")
    claim = AuthorityClaim(role=ApplicantRole.FINANCE_OFFICER, poa=_mchd(GUID_BUH))

    with pytest.raises(SourceUnavailable):
        await service.resolve(buh, await _company(fixtures, KHLEBNY_DVOR), claim)
