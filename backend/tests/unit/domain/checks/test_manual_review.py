"""Поводы для ручной проверки SCOPE раздел 4, этап 3: каждый на границе срабатывания."""

from datetime import date

import pytest

from app.domain.checks import (
    AmountOver5mTrigger,
    CbrYellowLevelTrigger,
    DirectorBailiffOver10kTrigger,
    ManualReviewTrigger,
    PipelineContext,
    RecentOwnerChangeTrigger,
    SecuredVariantTrigger,
    TaxDebtOver10kTrigger,
    UnreliableRegistryRecordTrigger,
    UnverifiedStatementTrigger,
    Verification,
    default_manual_review_triggers,
)
from app.domain.enums import KycLevel, RiskClass, Variant
from app.domain.scoring import ScoringResult
from app.domain.value_objects import BailiffInfo, Money
from tests.fakes.context import (
    DIRECTOR_INN,
    clean_tax,
    make_application,
    make_collateral,
    make_company,
    make_context,
    make_statement,
)

SCORING = ScoringResult(total=90, risk_class=RiskClass.A, debt_load=0.12)


def _applies(trigger: ManualReviewTrigger, ctx: PipelineContext) -> bool:
    return trigger.applies(ctx, SCORING)


def test_default_triggers_follow_scope_order() -> None:
    assert [type(t) for t in default_manual_review_triggers()] == [
        SecuredVariantTrigger,
        UnverifiedStatementTrigger,
        AmountOver5mTrigger,
        RecentOwnerChangeTrigger,
        UnreliableRegistryRecordTrigger,
        CbrYellowLevelTrigger,
        TaxDebtOver10kTrigger,
        DirectorBailiffOver10kTrigger,
    ]


def test_reason_codes_are_unique() -> None:
    codes = [t.reason_code for t in default_manual_review_triggers()]
    assert len(codes) == len(set(codes))


def test_healthy_application_has_no_reasons() -> None:
    ctx = make_context()
    assert not any(_applies(t, ctx) for t in default_manual_review_triggers())


@pytest.mark.parametrize(
    ("variant", "applies"), [(Variant.UNSECURED, False), (Variant.SECURED, True)]
)
def test_secured_variant(variant: Variant, applies: bool) -> None:
    application = make_application(
        variant=variant,
        collateral=make_collateral() if variant is Variant.SECURED else None,
    )
    ctx = make_context(application=application)
    assert _applies(SecuredVariantTrigger(), ctx) is applies


def test_unverified_statement() -> None:
    statement = make_statement()
    verified = make_context(statement=statement, statement_verification=Verification(verified=True))
    unverified = make_context(
        statement=statement, statement_verification=Verification(verified=False)
    )
    assert not _applies(UnverifiedStatementTrigger(), verified)
    assert _applies(UnverifiedStatementTrigger(), unverified)


def test_statement_without_verification_counts_as_unverified() -> None:
    ctx = make_context(statement=make_statement(), statement_verification=None)
    assert _applies(UnverifiedStatementTrigger(), ctx)


def test_no_statement_is_not_a_reason() -> None:
    assert not _applies(UnverifiedStatementTrigger(), make_context(statement=None))


@pytest.mark.parametrize(("amount", "applies"), [("5000000", False), ("5000000.01", True)])
def test_amount_over_5m(amount: str, applies: bool) -> None:
    ctx = make_context(application=make_application(amount=amount))
    assert _applies(AmountOver5mTrigger(), ctx) is applies


@pytest.mark.parametrize(
    ("changed_at", "applies"),
    [
        (None, False),
        (date(2026, 4, 24), False),  # ровно 6 месяцев назад
        (date(2026, 4, 25), True),
    ],
)
def test_recent_owner_change(changed_at: date | None, applies: bool) -> None:
    ctx = make_context(company=make_company(director_changed_at=changed_at))
    assert _applies(RecentOwnerChangeTrigger(), ctx) is applies


@pytest.mark.parametrize(("records", "applies"), [((), False), (("address",), True)])
def test_unreliable_registry_record(records: tuple[str, ...], applies: bool) -> None:
    ctx = make_context(company=make_company(unreliable_records=records))
    assert _applies(UnreliableRegistryRecordTrigger(), ctx) is applies


@pytest.mark.parametrize(
    ("level", "applies"),
    [(KycLevel.GREEN, False), (KycLevel.YELLOW, True)],
)
def test_cbr_yellow_level(level: KycLevel, applies: bool) -> None:
    ctx = make_context(cbr_level=level)
    assert _applies(CbrYellowLevelTrigger(), ctx) is applies


@pytest.mark.parametrize(("debt", "applies"), [("10000", False), ("10000.01", True)])
def test_tax_debt_over_10k(debt: str, applies: bool) -> None:
    ctx = make_context(tax=clean_tax(tax_debt=Money.of(debt)))
    assert _applies(TaxDebtOver10kTrigger(), ctx) is applies


@pytest.mark.parametrize(("debt", "applies"), [("10000", False), ("10000.01", True)])
def test_director_bailiff_over_10k(debt: str, applies: bool) -> None:
    ctx = make_context(
        director_bailiff=BailiffInfo(
            subject_inn=DIRECTOR_INN, open_proceedings=1, total_debt=Money.of(debt)
        )
    )
    assert _applies(DirectorBailiffOver10kTrigger(), ctx) is applies


def test_director_bailiff_unknown_is_not_a_reason() -> None:
    assert not _applies(DirectorBailiffOver10kTrigger(), make_context(director_bailiff=None))
