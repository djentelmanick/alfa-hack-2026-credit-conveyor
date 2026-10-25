"""Проверки формы SCOPE раздел 4, этап 1 (BACKEND_SPEC раздел 6, таблица FormValidator)."""

from typing import Any
from uuid import uuid4

import pytest
import yaml

from app.application.form_validator import FormValidator
from app.domain.entities import Application, Consent, User
from app.domain.enums import CheckOutcome, CompanyKind, ConsentType, Purpose, Role, Variant
from app.domain.errors import ActiveApplicationExists, NotDirector, ValidationFailed
from app.domain.products import ProductCatalog
from app.infra.mocks import MockEgrulAdapter, MockFailures, MockTurnoverAdapter, SyntheticData
from tests.fakes.auth import FixedClock, make_authority_service
from tests.fakes.context import make_application, make_collateral, make_statement
from tests.fakes.repositories import InMemoryApplicationRepository
from tests.fakes.scenarios import IVANOVA, KOLOS, NOW
from tests.unit.domain.conftest import PRODUCTS_YAML

LLC_CONSENTS = (ConsentType.BKI_COMPANY, ConsentType.BKI_PERSONAL, ConsentType.PERSONAL_DATA)
IE_CONSENTS = (ConsentType.BKI_PERSONAL, ConsentType.PERSONAL_DATA)


@pytest.fixture(scope="module")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


@pytest.fixture(scope="module")
def catalog() -> ProductCatalog:
    return ProductCatalog.from_config(yaml.safe_load(PRODUCTS_YAML.read_text(encoding="utf-8")))


@pytest.fixture
def validator(fixtures: SyntheticData, catalog: ProductCatalog) -> FormValidator:
    failures = MockFailures()
    return FormValidator(
        catalog,
        MockEgrulAdapter(fixtures, failures),
        MockTurnoverAdapter(fixtures, failures),
        make_authority_service(fixtures, failures, FixedClock()),
    )


def _user(full_name: str, inn: str) -> User:
    return User(
        id=uuid4(),
        login="client",
        password_hash="x",
        role=Role.CLIENT,
        full_name=full_name,
        inn=inn,
        passport_verified=True,
        created_at=NOW,
    )


@pytest.fixture
def kolos_director(fixtures: SyntheticData) -> User:
    company = fixtures.company(KOLOS)
    assert company is not None
    assert company.egrul.director is not None
    return _user(company.egrul.director.full_name, company.egrul.director.inn)


@pytest.fixture
def ivanova(fixtures: SyntheticData) -> User:
    company = fixtures.company(IVANOVA)
    assert company is not None
    return _user(company.egrul.full_name, company.egrul.inn)


def _consents(user: User, types: tuple[ConsentType, ...]) -> tuple[Consent, ...]:
    return tuple(Consent(type=t, given_by=user.id, given_at=NOW) for t in types)


def _kolos(user: User, **overrides: Any) -> Application:
    fields: dict[str, Any] = {
        "inn": KOLOS,
        "amount": "2000000",
        "term_months": 12,
        "consents": _consents(user, LLC_CONSENTS),
    }
    fields.update(overrides)
    return make_application(**fields)


def _ivanova(user: User, **overrides: Any) -> Application:
    fields: dict[str, Any] = {
        "inn": IVANOVA,
        "kind": CompanyKind.IE,
        "amount": "3000000",
        "term_months": 24,
        "owner_guarantee": False,
        "consents": _consents(user, IE_CONSENTS),
        "statements": (make_statement(inn=IVANOVA),),
    }
    fields.update(overrides)
    return make_application(**fields)


async def _fields(
    validator: FormValidator, application: Application, user: User
) -> set[tuple[str, str]]:
    with pytest.raises(ValidationFailed) as exc:
        await validator.validate(application, user, InMemoryApplicationRepository(application))
    return {(d.field, d.code) for d in exc.value.details}


async def test_valid_application_passes_every_check(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(kolos_director)
    checks = await validator.validate(
        application, kolos_director, InMemoryApplicationRepository(application)
    )
    assert all(c.outcome is CheckOutcome.PASSED for c in checks)
    assert {c.code for c in checks} == {
        "FORM_COMPLETE",
        "TERMS_IN_RANGE",
        "PURPOSE_ALLOWED",
        "APPLICANT_AUTHORITY",
        "NO_ACTIVE_APPLICATION",
        "COLLATERAL_COVERAGE",
        "OWNER_GUARANTEE",
        "CONSENTS_GIVEN",
        "STATEMENT_UPLOADED",
        "STATEMENT_INN_MATCH",
    }


async def test_valid_individual_entrepreneur_with_statement(
    validator: FormValidator, ivanova: User
) -> None:
    application = _ivanova(ivanova)
    checks = await validator.validate(application, ivanova, InMemoryApplicationRepository())
    assert all(c.passed for c in checks)


async def test_form_complete_checks_inn_format(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(kolos_director, kind=CompanyKind.IE)  # у ИП ИНН из 12 цифр
    assert ("inn", "FORM_COMPLETE") in await _fields(validator, application, kolos_director)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"amount": "299999.99"}, "amount"),
        ({"amount": "30000000.01"}, "amount"),
        ({"term_months": 11}, "term_months"),
        ({"term_months": 61}, "term_months"),
    ],
)
async def test_terms_in_range(
    validator: FormValidator, kolos_director: User, overrides: dict[str, object], field: str
) -> None:
    application = _kolos(kolos_director, **overrides)
    assert (field, "TERMS_IN_RANGE") in await _fields(validator, application, kolos_director)


async def test_terms_on_the_boundary_pass(validator: FormValidator, kolos_director: User) -> None:
    for amount, term in (("300000", 12), ("30000000", 60)):
        application = _kolos(kolos_director, amount=amount, term_months=term)
        await validator.validate(application, kolos_director, InMemoryApplicationRepository())


async def test_revolving_line_only_for_working_capital(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(
        kolos_director, purpose=Purpose.DEVELOPMENT, product_code="REVOLVING_LINE", term_months=18
    )
    assert ("product_code", "PURPOSE_ALLOWED") in await _fields(
        validator, application, kolos_director
    )


async def test_applicant_must_be_director(validator: FormValidator) -> None:
    stranger = _user("Посторонний Человек", "771602345657")
    application = _kolos(stranger)
    with pytest.raises(NotDirector):
        await validator.validate(application, stranger, InMemoryApplicationRepository())


async def test_no_other_active_application(validator: FormValidator, kolos_director: User) -> None:
    other = _kolos(kolos_director)
    application = _kolos(kolos_director)
    with pytest.raises(ActiveApplicationExists):
        await validator.validate(
            application, kolos_director, InMemoryApplicationRepository(other, application)
        )


@pytest.mark.parametrize(
    ("collateral_value", "field"),
    [(None, "collateral"), ("5999999.99", "collateral.estimated_value")],
)
async def test_collateral_covers_half_of_amount(
    validator: FormValidator, kolos_director: User, collateral_value: str | None, field: str
) -> None:
    application = _kolos(kolos_director, variant=Variant.SECURED, amount="12000000", term_months=24)
    # Через конструктор: create_draft не пустил бы залог с недостаточной оценкой.
    application.collateral = make_collateral(collateral_value) if collateral_value else None
    assert (field, "COLLATERAL_COVERAGE") in await _fields(validator, application, kolos_director)


async def test_collateral_of_exactly_half_passes(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(
        kolos_director,
        variant=Variant.SECURED,
        amount="12000000",
        term_months=24,
        owner_guarantee=False,
        collateral=make_collateral("6000000"),
    )
    await validator.validate(application, kolos_director, InMemoryApplicationRepository())


async def test_llc_without_collateral_needs_owner_guarantee(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(kolos_director, owner_guarantee=False)
    assert ("owner_guarantee", "OWNER_GUARANTEE") in await _fields(
        validator, application, kolos_director
    )


@pytest.mark.parametrize(
    "missing", [ConsentType.BKI_COMPANY, ConsentType.BKI_PERSONAL, ConsentType.PERSONAL_DATA]
)
async def test_llc_needs_every_consent(
    validator: FormValidator, kolos_director: User, missing: ConsentType
) -> None:
    types = tuple(t for t in LLC_CONSENTS if t is not missing)
    application = _kolos(kolos_director, consents=_consents(kolos_director, types))
    assert ("consents", "CONSENTS_GIVEN") in await _fields(validator, application, kolos_director)


async def test_individual_entrepreneur_needs_one_bki_consent(
    validator: FormValidator, ivanova: User
) -> None:
    application = _ivanova(ivanova, consents=_consents(ivanova, (ConsentType.PERSONAL_DATA,)))
    assert ("consents", "CONSENTS_GIVEN") in await _fields(validator, application, ivanova)


async def test_statement_required_without_account(validator: FormValidator, ivanova: User) -> None:
    application = _ivanova(ivanova, statements=())
    assert ("statements", "STATEMENT_UPLOADED") in await _fields(validator, application, ivanova)


async def test_statement_must_belong_to_applicant(validator: FormValidator, ivanova: User) -> None:
    application = _ivanova(ivanova, statements=(make_statement(inn="770000000000"),))
    assert ("statements", "STATEMENT_INN_MATCH") in await _fields(validator, application, ivanova)


async def test_all_field_errors_are_reported_together(
    validator: FormValidator, kolos_director: User
) -> None:
    application = _kolos(kolos_director, amount="100000", owner_guarantee=False, consents=())
    assert {field for field, _ in await _fields(validator, application, kolos_director)} >= {
        "amount",
        "owner_guarantee",
        "consents",
    }
