"""Агрегат сохраняется и читается целиком, а повторное сохранение отражает изменения домена."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.entities import (
    Application,
    AuditEntry,
    CheckResult,
    Consent,
    ContractChange,
    ContractVersion,
    CreditLineContract,
    Decision,
    LoanContract,
    OfferOption,
    Operation,
    ScheduleItem,
    SourceSnapshot,
    Tranche,
    User,
    UserCompany,
)
from app.domain.enums import (
    ApplicationStatus,
    CheckOutcome,
    CheckStage,
    CompanyKind,
    ConsentType,
    ContractStatus,
    DecisionOutcome,
    FetchStatus,
    OperationType,
    RiskClass,
    Role,
    SourceType,
    Variant,
)
from app.domain.errors import LoginTaken, NotFound
from app.domain.ports import AnalystFilter
from app.domain.scoring.result import FactorScore, ScoringResult
from app.domain.value_objects import Money
from app.infra.db.models import AuditEntryRow
from app.infra.db.uow import SqlUnitOfWork
from tests.fakes.analyst import manual_review_application
from tests.fakes.auth import make_user
from tests.fakes.context import make_application, make_collateral, make_statement
from tests.fakes.scenarios import KOLOS, NOW, TECHPROM

type UowFactory = Callable[[], SqlUnitOfWork]

APPLICATION_FIELDS = (
    "id",
    "applicant_id",
    "inn",
    "company_kind",
    "purpose",
    "product_code",
    "variant",
    "amount",
    "term_months",
    "owner_guarantee",
    "collateral",
    "status",
    "created_at",
    "updated_at",
    "expires_at",
    "accepted_offer_id",
    "contract_id",
    "consents",
    "statements",
    "snapshots",
    "checks",
    "scoring",
    "decisions",
    "info_requests",
    "history",
)


def assert_same_application(actual: Application, expected: Application) -> None:
    for field in APPLICATION_FIELDS:
        assert getattr(actual, field) == getattr(expected, field), field


async def _saved_user(uow: UowFactory, login: str = "kolos") -> User:
    user = await make_user(login=login)
    async with uow() as unit:
        await unit.users.add(user)
        await unit.commit()
    return user


def _full_application(applicant_id: UUID) -> Application:
    """Заявка, в которой заполнены все части агрегата."""
    app = make_application(
        inn=TECHPROM,
        variant=Variant.SECURED,
        product_code="REVOLVING_LINE",
        amount="12000000",
        term_months=60,
        owner_guarantee=False,
        collateral=make_collateral(),
        consents=(
            Consent(type=ConsentType.BKI_COMPANY, given_by=applicant_id, given_at=NOW),
            Consent(type=ConsentType.PERSONAL_DATA, given_by=applicant_id, given_at=NOW),
        ),
        statements=(make_statement(inn=TECHPROM), make_statement(inn=TECHPROM)),
    )
    app.applicant_id = applicant_id
    analyst_id = uuid4()
    app.transition_to(
        ApplicationStatus.SUBMITTED, actor_id=applicant_id, reason="отправлена", now=NOW
    )
    app.transition_to(ApplicationStatus.CHECKING, actor_id=None, reason="проверка", now=NOW)
    app.snapshots.append(
        SourceSnapshot(
            source=SourceType.EGRUL,
            status=FetchStatus.OK,
            attempts=2,
            payload={"status": "active", "founders": [{"name": "Кузнецов", "share": 55}]},
            fetched_at=NOW,
        )
    )
    app.checks.append(
        CheckResult(
            code="COMPANY_INACTIVE",
            stage=CheckStage.CHECKING,
            expected="ACTIVE",
            actual="ACTIVE",
            outcome=CheckOutcome.PASSED,
        )
    )
    app.scoring = ScoringResult(
        total=100,
        risk_class=RiskClass.A,
        debt_load=0.15,
        factors=(FactorScore("AGE", "Возраст бизнеса", "7 лет", 20),),
    )
    app.transition_to(ApplicationStatus.SCORING, actor_id=None, reason="скоринг", now=NOW)
    app.transition_to(ApplicationStatus.MANUAL_REVIEW, actor_id=None, reason="залог", now=NOW)
    app.decisions.append(
        Decision(
            id=uuid4(),
            rule_code="MANUAL_REVIEW",
            outcome=DecisionOutcome.COUNTER_OFFER,
            rate=Decimal("26.5"),
            reasons=("SECURED_VARIANT", "AMOUNT_OVER_5M"),
            offers=(
                OfferOption(
                    id=uuid4(),
                    product_code="LUMP_SUM",
                    variant=Variant.UNSECURED,
                    amount=Money.of(3_600_000),
                    term_months=24,
                    rate=Decimal("26.5"),
                ),
                OfferOption(
                    id=uuid4(),
                    product_code="LUMP_SUM",
                    variant=Variant.SECURED,
                    amount=Money.of(4_500_000),
                    term_months=24,
                    rate=Decimal("24.5"),
                ),
            ),
            decided_by=analyst_id,
            comment="Контрпредложение",
            decided_at=NOW,
        )
    )
    app.request_info(["STATEMENT"], "Нужна выписка за 6 месяцев", actor_id=analyst_id, now=NOW)
    app.info_requests[-1].item_messages = {"STATEMENT": "Выписка за 12 месяцев с подписью банка"}
    app.info_requests[-1].resolved_at = NOW + timedelta(hours=1)
    return app


# --- Пользователи ---


async def test_user_round_trip_with_companies(uow: UowFactory) -> None:
    user = await make_user(
        login="kolos",
        role=Role.ANALYST,
        companies=(
            UserCompany(inn=KOLOS, name="ООО «Колос»", kind=CompanyKind.LLC, is_director=True),
            UserCompany(inn="770498765403", name="ИП", kind=CompanyKind.IE, is_director=False),
        ),
    )
    async with uow() as unit:
        await unit.users.add(user)
        await unit.commit()

    async with uow() as unit:
        by_id = await unit.users.get(user.id)
        by_login = await unit.users.get_by_login("kolos")

    assert by_id is by_login
    assert vars(by_id) == vars(user)


async def test_user_changes_are_saved_on_commit(uow: UowFactory) -> None:
    user = await _saved_user(uow)

    async with uow() as unit:
        loaded = await unit.users.get(user.id)
        loaded.link_company(
            UserCompany(inn=KOLOS, name="ООО «Колос»", kind=CompanyKind.LLC, is_director=True)
        )
        loaded.link_company(
            UserCompany(
                inn=KOLOS, name="ООО «Колос» (новое)", kind=CompanyKind.LLC, is_director=True
            )
        )
        await unit.commit()

    async with uow() as unit:
        companies = (await unit.users.get(user.id)).companies
    assert [c.name for c in companies] == ["ООО «Колос» (новое)"]


async def test_unknown_user(uow: UowFactory) -> None:
    async with uow() as unit:
        assert await unit.users.get_by_login("nobody") is None
        with pytest.raises(NotFound):
            await unit.users.get(uuid4())


async def test_uncommitted_changes_are_discarded(uow: UowFactory) -> None:
    async with uow() as unit:
        await unit.users.add(await make_user(login="ghost"))

    async with uow() as unit:
        assert await unit.users.get_by_login("ghost") is None


async def test_concurrent_registration_with_same_login_is_login_taken(uow: UowFactory) -> None:
    async with uow() as first, uow() as second:
        await first.users.add(await make_user(login="twin"))
        await second.users.add(await make_user(login="twin"))
        await first.commit()
        with pytest.raises(LoginTaken):
            await second.commit()


# --- Согласия и аудит ---


async def test_consents_are_stored_per_inn_and_user(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    other = await _saved_user(uow, login="other")
    consent = Consent(type=ConsentType.BKI_PERSONAL, given_by=user.id, given_at=NOW)

    async with uow() as unit:
        await unit.consents.add(KOLOS, consent)
        await unit.consents.add(TECHPROM, replace(consent, type=ConsentType.PERSONAL_DATA))
        await unit.commit()

    async with uow() as unit:
        assert await unit.consents.list_for(KOLOS, user.id) == [consent]
        assert await unit.consents.list_for(KOLOS, other.id) == []


async def test_repeated_consent_replaces_time(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    consent = Consent(type=ConsentType.PERSONAL_DATA, given_by=user.id, given_at=NOW)
    later = replace(consent, given_at=NOW + timedelta(days=1))

    async with uow() as unit:
        await unit.consents.add(KOLOS, consent)
        await unit.commit()
    async with uow() as unit:
        await unit.consents.add(KOLOS, later)
        await unit.commit()

    async with uow() as unit:
        assert await unit.consents.list_for(KOLOS, user.id) == [later]


async def test_audit_entry_is_saved(uow: UowFactory, engine: AsyncEngine) -> None:
    entry = AuditEntry(
        id=uuid4(),
        actor_id=uuid4(),
        action="APPROVE",
        target_id=uuid4(),
        details={"comment": "ok", "rate": "23.0"},
        at=NOW,
    )
    async with uow() as unit:
        await unit.audit.add(entry)
        await unit.commit()

    async with engine.connect() as conn:
        row = (await conn.execute(select(AuditEntryRow.__table__))).one()
    assert (row.action, row.details, row.at) == ("APPROVE", {"comment": "ok", "rate": "23.0"}, NOW)


async def test_audit_entries_are_read_per_target_in_order(uow: UowFactory) -> None:
    target = uuid4()
    entries = [
        AuditEntry(
            id=uuid4(),
            actor_id=uuid4(),
            action=action,
            target_id=target,
            details={"comment": action},
            at=NOW + timedelta(minutes=i),
        )
        for i, action in enumerate(["RECHECK", "APPROVE"])
    ]
    other = AuditEntry(id=uuid4(), actor_id=None, action="REJECT", target_id=uuid4(), at=NOW)
    async with uow() as unit:
        for entry in [entries[1], other, entries[0]]:
            await unit.audit.add(entry)
        await unit.commit()

    async with uow() as unit:
        assert await unit.audit.list_for(target) == entries


# --- Заявка ---


async def test_application_round_trip_with_all_parts(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    app = _full_application(user.id)

    async with uow() as unit:
        await unit.applications.add(app)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.applications.get(app.id)

    assert_same_application(loaded, app)
    assert loaded.status is ApplicationStatus.NEEDS_INFO
    assert len(loaded.history) == 5
    assert loaded.statements[0].operations == app.statements[0].operations


async def test_application_changes_are_saved_on_commit(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    app = _full_application(user.id)
    async with uow() as unit:
        await unit.applications.add(app)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.applications.get(app.id)
        loaded.statements.pop()
        loaded.decisions[-1] = replace(loaded.decisions[-1], offers=loaded.decisions[-1].offers[:1])
        loaded.collateral = None
        loaded.scoring = None
        loaded.transition_to(
            ApplicationStatus.CANCELLED, actor_id=user.id, reason="передумал", now=NOW
        )
        await unit.commit()
        expected = loaded

    async with uow() as unit:
        reloaded = await unit.applications.get(app.id)

    assert_same_application(reloaded, expected)
    assert len(reloaded.statements) == 1
    assert len(reloaded.decisions[0].offers) == 1
    assert reloaded.history[-1].to_status is ApplicationStatus.CANCELLED


async def test_same_aggregate_within_unit_of_work(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    app = make_application()
    app.applicant_id = user.id
    async with uow() as unit:
        await unit.applications.add(app)
        await unit.commit()

    async with uow() as unit:
        assert await unit.applications.get(app.id) is await unit.applications.get(app.id)


async def test_find_active_by_inn_ignores_final_applications(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    cancelled = make_application(inn=KOLOS)
    cancelled.applicant_id = user.id
    cancelled.cancel(actor_id=user.id, reason="передумал", now=NOW)
    async with uow() as unit:
        await unit.applications.add(cancelled)
        await unit.commit()

    async with uow() as unit:
        assert await unit.applications.find_active_by_inn(KOLOS) is None

    active = make_application(inn=KOLOS)
    active.applicant_id = user.id
    async with uow() as unit:
        await unit.applications.add(active)
        await unit.commit()

    async with uow() as unit:
        found = await unit.applications.find_active_by_inn(KOLOS)
        assert found is not None
        assert found.id == active.id
        assert found is await unit.applications.get(active.id)
        assert await unit.applications.find_active_by_inn(TECHPROM) is None


async def test_unknown_application(uow: UowFactory) -> None:
    async with uow() as unit:
        with pytest.raises(NotFound):
            await unit.applications.get(uuid4())


async def test_list_for_analyst_filters_and_orders(uow: UowFactory) -> None:
    user = await _saved_user(uow)
    draft = make_application(inn=KOLOS)
    older = manual_review_application(applicant_id=user.id, inn=TECHPROM)
    newer = manual_review_application(applicant_id=user.id, inn=KOLOS)
    newer.created_at = NOW + timedelta(hours=1)
    newer.record_scoring(ScoringResult(total=90, risk_class=RiskClass.A, debt_load=0.12))
    rejected = manual_review_application(applicant_id=user.id, inn=KOLOS)
    rejected.created_at = NOW + timedelta(hours=2)
    rejected.reject_by_analyst(
        "TAX_DEBT_OVER_10K", actor_id=uuid4(), comment="долг", now=NOW + timedelta(hours=3)
    )
    draft.applicant_id = user.id
    async with uow() as unit:
        for app in (draft, older, newer, rejected):
            await unit.applications.add(app)
        await unit.commit()

    async with uow() as unit:
        repo = unit.applications
        queue, total = await repo.list_for_analyst(AnalystFilter(manual_queue=True))
        assert ([a.id for a in queue], total) == ([older.id, newer.id], 2)

        everything, total = await repo.list_for_analyst(AnalystFilter(), offset=0, limit=1)
        assert ([a.id for a in everything], total) == ([rejected.id], 3)

        by_class, _ = await repo.list_for_analyst(AnalystFilter(risk_class=RiskClass.A))
        assert [a.id for a in by_class] == [newer.id]

        by_inn, _ = await repo.list_for_analyst(
            AnalystFilter(inn=KOLOS, status=ApplicationStatus.REJECTED)
        )
        assert [a.id for a in by_inn] == [rejected.id]


# --- Договоры ---


async def _saved_application(uow: UowFactory) -> Application:
    user = await _saved_user(uow)
    app = make_application()
    app.applicant_id = user.id
    async with uow() as unit:
        await unit.applications.add(app)
        await unit.commit()
    return app


def _version(no: int) -> ContractVersion:
    return ContractVersion(
        version_no=no,
        document_key=f"contracts/v{no}.pdf",
        changes=(ContractChange("rate", "26.5", "23.5"),) if no > 1 else (),
        reason="Условия изменены аналитиком" if no > 1 else "Договор подписан",
        schedule=tuple(
            ScheduleItem(
                due_date=date(2026, 11 + i, 24),
                principal=Money.of("160000.00"),
                interest=Money.of("39166.67"),
                balance=Money.of(2_000_000 - 160_000 * (i + 1)),
            )
            for i in range(2)
        ),
        author_id=None if no == 1 else uuid4(),
        created_at=NOW,
    )


async def test_loan_contract_round_trip(uow: UowFactory) -> None:
    app = await _saved_application(uow)
    contract = LoanContract(
        id=uuid4(),
        application_id=app.id,
        number="КД-2026-000001",
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        rate=Decimal("23.5"),
        term_months=12,
        start_date=date(2026, 10, 24),
        contract_date=date(2026, 10, 24),
        versions=[_version(1), _version(2)],
        operations=[
            Operation(
                id=uuid4(),
                type=OperationType.DISBURSEMENT,
                amount=Money.of(2_000_000),
                contract_date=date(2026, 10, 24),
                actor_id=None,
                balance_after=Money.of(2_000_000),
            )
        ],
        principal=Money.of(2_000_000),
        outstanding=Money.of(2_000_000),
    )
    async with uow() as unit:
        await unit.contracts.add(contract)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.contracts.get(contract.id)

    assert isinstance(loaded, LoanContract)
    assert vars(loaded) == vars(contract)


async def test_credit_line_changes_are_saved(uow: UowFactory) -> None:
    app = await _saved_application(uow)
    tranche = Tranche(
        id=uuid4(),
        amount=Money.of(3_000_000),
        issued_at=date(2026, 10, 24),
        due_at=date(2027, 4, 24),
        outstanding=Money.of(3_000_000),
    )
    line = CreditLineContract(
        id=uuid4(),
        application_id=app.id,
        number="ВКЛ-2026-000001",
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        rate=Decimal("23.0"),
        term_months=60,
        start_date=date(2026, 10, 24),
        contract_date=date(2026, 10, 24),
        versions=[_version(1)],
        limit=Money.of(12_000_000),
        available=Money.of(9_000_000),
        tranches=[tranche],
    )
    async with uow() as unit:
        await unit.contracts.add(line)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.contracts.get(line.id)
        assert isinstance(loaded, CreditLineContract)
        loaded.tranches[0].outstanding = Money.of(1_000_000)
        loaded.available = Money.of(11_000_000)
        loaded.operations.append(
            Operation(
                id=uuid4(),
                type=OperationType.TRANCHE_REPAID,
                amount=Money.of(2_000_000),
                contract_date=date(2026, 12, 24),
                actor_id=app.applicant_id,
                balance_after=Money.of(1_000_000),
            )
        )
        loaded.status = ContractStatus.CLOSED
        await unit.commit()
        expected = vars(loaded).copy()

    async with uow() as unit:
        reloaded = await unit.contracts.get(line.id)

    assert vars(reloaded) == expected
    assert reloaded.debt() == Money.of(1_000_000)


async def test_operation_keeps_tranche_and_emulator_mark(uow: UowFactory) -> None:
    app = await _saved_application(uow)
    tranche_id = uuid4()
    line = CreditLineContract(
        id=uuid4(),
        application_id=app.id,
        number="КД-2026-000002",
        product_code="REVOLVING_LINE",
        variant=Variant.SECURED,
        rate=Decimal("23.0"),
        term_months=60,
        start_date=date(2026, 10, 24),
        contract_date=date(2026, 11, 24),
        versions=[_version(1)],
        operations=[
            Operation(
                id=uuid4(),
                type=OperationType.TRANCHE_DRAWN,
                amount=Money.of(1_000_000),
                contract_date=date(2026, 10, 24),
                actor_id=app.applicant_id,
                balance_after=Money.of(1_000_000),
                tranche_id=tranche_id,
            ),
            Operation(
                id=uuid4(),
                type=OperationType.INTEREST_ACCRUED,
                amount=Money.of("19166.67"),
                contract_date=date(2026, 11, 24),
                actor_id=None,
                balance_after=Money.of(1_000_000),
                tranche_id=tranche_id,
                emulated=True,
            ),
        ],
        limit=Money.of(12_000_000),
        available=Money.of(11_000_000),
    )
    async with uow() as unit:
        await unit.contracts.add(line)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.contracts.get(line.id)

    assert loaded.operations == line.operations


async def test_removed_versions_and_operations_are_deleted(uow: UowFactory) -> None:
    app = await _saved_application(uow)
    disbursement = Operation(
        id=uuid4(),
        type=OperationType.DISBURSEMENT,
        amount=Money.of(2_000_000),
        contract_date=date(2026, 10, 24),
        actor_id=None,
        balance_after=Money.of(2_000_000),
    )
    contract = LoanContract(
        id=uuid4(),
        application_id=app.id,
        number="КД-2026-000003",
        product_code="LUMP_SUM",
        variant=Variant.UNSECURED,
        rate=Decimal("23.5"),
        term_months=12,
        start_date=date(2026, 10, 24),
        contract_date=date(2026, 11, 24),
        versions=[_version(1), _version(2)],
        operations=[
            disbursement,
            replace(disbursement, id=uuid4(), type=OperationType.SCHEDULED_PAYMENT),
        ],
        principal=Money.of(2_000_000),
        outstanding=Money.of(1_840_000),
    )
    async with uow() as unit:
        await unit.contracts.add(contract)
        await unit.commit()

    async with uow() as unit:
        loaded = await unit.contracts.get(contract.id)
        del loaded.versions[1:]
        del loaded.operations[1:]
        await unit.commit()

    async with uow() as unit:
        reloaded = await unit.contracts.get(contract.id)

    assert [v.version_no for v in reloaded.versions] == [1]
    assert reloaded.operations == [disbursement]


async def test_unknown_contract(uow: UowFactory) -> None:
    async with uow() as unit:
        with pytest.raises(NotFound):
            await unit.contracts.get(uuid4())


async def test_audit_table_is_empty_without_commit(uow: UowFactory, engine: AsyncEngine) -> None:
    async with uow() as unit:
        await unit.audit.add(
            AuditEntry(id=uuid4(), actor_id=None, action="REJECT", target_id=uuid4(), at=NOW)
        )

    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(AuditEntryRow)) == 0
