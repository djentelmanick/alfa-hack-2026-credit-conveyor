"""Кабинет аналитика: очередь, отчёт и решения по заявкам на ручной проверке (SCOPE, раздел 7)."""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.application.application_service import PipelineJob, load_history
from app.application.contract_service import ContractDocuments
from app.application.report_builder import ApplicationReport, ReportBuilder
from app.domain.decision import RatePolicy
from app.domain.entities import Application, AuditEntry, Contract, OfferOption, TokenClaims
from app.domain.enums import ApplicationStatus, DecisionOutcome, Role, Variant
from app.domain.errors import FieldError, NotFound, ValidationFailed
from app.domain.ports import AnalystFilter, Clock, StatusPublisher, TaskRunner, UnitOfWork
from app.domain.products import LoanProduct, ProductCatalog
from app.domain.value_objects import Money

logger = logging.getLogger(__name__)


class AuditAction:
    APPROVE = "APPROVE"
    COUNTER_OFFER = "COUNTER_OFFER"
    REQUEST_INFO = "REQUEST_INFO"
    REJECT = "REJECT"
    RECHECK = "RECHECK"
    EDIT_CONTRACT = "EDIT_CONTRACT"


CONTRACT_EDIT_REASON = "Условия изменены аналитиком"


@dataclass(frozen=True, kw_only=True, slots=True)
class OfferTerms:
    product_code: str
    variant: Variant
    amount: Money
    term_months: int
    rate: Decimal


@dataclass(frozen=True, kw_only=True, slots=True)
class AnalystListEntry:
    application: Application
    manual_reasons: tuple[str, ...]
    submitted_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class AnalystPage:
    items: list[AnalystListEntry]
    total: int
    page: int
    size: int


class AnalystService:
    """Каждое действие: изменение агрегата, запись в аудит, commit, затем публикация событий."""

    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        catalog: ProductCatalog,
        rates: RatePolicy,
        reports: ReportBuilder,
        runner: TaskRunner,
        pipeline: PipelineJob,
        publisher: StatusPublisher,
        clock: Clock,
        offer_ttl: timedelta,
        documents: ContractDocuments,
    ) -> None:
        self._documents = documents
        self._uow = uow
        self._catalog = catalog
        self._rates = rates
        self._reports = reports
        self._runner = runner
        self._pipeline = pipeline
        self._publisher = publisher
        self._clock = clock
        self._offer_ttl = offer_ttl

    async def queue(self, flt: AnalystFilter, page: int, size: int) -> AnalystPage:
        async with self._uow() as uow:
            items, total = await uow.applications.list_for_analyst(
                flt, offset=(page - 1) * size, limit=size
            )
        return AnalystPage(items=[_list_entry(a) for a in items], total=total, page=page, size=size)

    async def report(self, application_id: UUID) -> ApplicationReport:
        async with self._uow() as uow:
            app = await uow.applications.get(application_id)
            applicant = await uow.users.get(app.applicant_id)
            history = await load_history(uow, app)
            contract = (
                await uow.contracts.get(app.contract_id) if app.contract_id is not None else None
            )
        return self._reports.build(
            app, Role.ANALYST, applicant=applicant, history=history, contract=contract
        )

    async def approve(
        self, claims: TokenClaims, application_id: UUID, rate: Decimal, comment: str
    ) -> Application:
        def change(app: Application) -> dict[str, str]:
            product = self._catalog.get(app.product_code)
            self._rates.validate_manual(product.variant(app.variant), rate)
            app.approve_by_analyst(
                rate,
                actor_id=claims.user_id,
                comment=comment,
                now=self._clock.now(),
                offer_ttl=self._offer_ttl,
            )
            return {"rate": format(rate, "f")}

        return await self._act(claims, application_id, AuditAction.APPROVE, comment, change)

    async def counter_offer(
        self,
        claims: TokenClaims,
        application_id: UUID,
        options: Sequence[OfferTerms],
        comment: str,
    ) -> Application:
        def change(app: Application) -> dict[str, str]:
            offers = self._validated_offers(app, options)
            app.counter_offer_by_analyst(
                offers,
                actor_id=claims.user_id,
                comment=comment,
                now=self._clock.now(),
                offer_ttl=self._offer_ttl,
            )
            return {
                f"option_{i}": f"{o.product_code} {o.variant} {o.amount} {o.term_months} {o.rate}"
                for i, o in enumerate(offers, start=1)
            }

        return await self._act(claims, application_id, AuditAction.COUNTER_OFFER, comment, change)

    async def request_info(
        self,
        claims: TokenClaims,
        application_id: UUID,
        items: Mapping[str, str],
        comment: str,
    ) -> Application:
        def change(app: Application) -> dict[str, str]:
            if not items:
                raise ValidationFailed(
                    [FieldError("items", "REQUIRED", "Укажите, каких данных не хватает")]
                )
            app.request_info_by_analyst(
                items, actor_id=claims.user_id, comment=comment, now=self._clock.now()
            )
            return {"items": ", ".join(items)}

        return await self._act(claims, application_id, AuditAction.REQUEST_INFO, comment, change)

    async def reject(
        self, claims: TokenClaims, application_id: UUID, reason_code: str, comment: str
    ) -> Application:
        def change(app: Application) -> dict[str, str]:
            app.reject_by_analyst(
                reason_code, actor_id=claims.user_id, comment=comment, now=self._clock.now()
            )
            return {"reason_code": reason_code}

        return await self._act(claims, application_id, AuditAction.REJECT, comment, change)

    async def recheck(self, claims: TokenClaims, application_id: UUID, comment: str) -> Application:
        def change(app: Application) -> dict[str, str]:
            app.start_recheck(actor_id=claims.user_id, comment=comment, now=self._clock.now())
            return {}

        app = await self._act(claims, application_id, AuditAction.RECHECK, comment, change)
        self._runner.enqueue(self._pipeline, app.id)
        return app

    async def edit_contract(
        self,
        claims: TokenClaims,
        contract_id: UUID,
        rate: Decimal | None,
        term_months: int | None,
        comment: str,
    ) -> Contract:
        """Новая версия с новым PDF. Сумму и лимит не меняем: это новое кредитное решение."""
        if not comment.strip():
            raise ValidationFailed(
                [FieldError("comment", "REQUIRED", "Комментарий к правке обязателен")]
            )
        if rate is None and term_months is None:
            raise ValidationFailed(
                [FieldError("rate", "REQUIRED", "Укажите новую ставку или новый срок")]
            )
        now = self._clock.now()
        documents = self._documents
        async with self._uow() as uow:
            contract = await uow.contracts.get(contract_id)
            contract.ensure_active()
            app = await uow.applications.get(contract.application_id)
            product = self._catalog.get(contract.product_code)
            new_rate = contract.rate if rate is None else rate
            new_term = contract.term_months if term_months is None else term_months
            self._validate_contract_terms(product, contract, new_rate, new_term)
            contract.edit_terms(
                rate=new_rate,
                term_months=new_term,
                schedule=product.reschedule(contract, new_rate, new_term),
                document_key=documents.key(contract.id, contract.next_version_no),
                reason=CONTRACT_EDIT_REASON,
                author_id=claims.user_id,
                now=now,
            )
            details = {
                "comment": comment.strip(),
                "version_no": str(contract.current_version.version_no),
            }
            if rate is not None:
                details["rate"] = format(rate, "f")
            if term_months is not None:
                details["term_months"] = str(term_months)
            await uow.audit.add(
                AuditEntry(
                    id=uuid4(),
                    actor_id=claims.user_id,
                    action=AuditAction.EDIT_CONTRACT,
                    target_id=contract.id,
                    details=details,
                    at=now,
                )
            )
            await documents.commit_with_document(uow, documents.data(contract, app, product))
        logger.info(
            "Аналитик %s изменил договор %s, версия %s",
            claims.user_id,
            contract.number,
            contract.current_version.version_no,
        )
        return contract

    def _validate_contract_terms(
        self, product: LoanProduct, contract: Contract, rate: Decimal, term_months: int
    ) -> None:
        terms = product.variant(contract.variant)
        errors: list[FieldError] = []
        try:
            self._rates.validate_manual(terms, rate)
        except ValidationFailed as exc:
            errors.extend(exc.details)
        if not terms.term_min <= term_months <= terms.term_max:
            errors.append(
                FieldError(
                    "term_months",
                    "TERMS_IN_RANGE",
                    f"Срок должен быть от {terms.term_min} до {terms.term_max} мес",
                )
            )
        if errors:
            raise ValidationFailed(errors)

    async def _act(
        self,
        claims: TokenClaims,
        application_id: UUID,
        action: str,
        comment: str,
        change: Callable[[Application], dict[str, str]],
    ) -> Application:
        async with self._uow() as uow:
            app = await uow.applications.get(application_id)
            details = change(app)
            await uow.audit.add(
                AuditEntry(
                    id=uuid4(),
                    actor_id=claims.user_id,
                    action=action,
                    target_id=app.id,
                    details={"comment": comment.strip(), **details},
                    at=self._clock.now(),
                )
            )
            await uow.commit()
        for event in app.pull_events():
            await self._publisher.publish(event)
        logger.info("Аналитик %s: %s по заявке %s", claims.user_id, action, app.id)
        return app

    def _validated_offers(
        self, app: Application, options: Sequence[OfferTerms]
    ) -> list[OfferOption]:
        """Все ошибки по вариантам разом, с индексом варианта в имени поля."""
        errors: list[FieldError] = []
        for i, option in enumerate(options):
            errors.extend(self._option_errors(app, f"options[{i}]", option))
        if errors:
            raise ValidationFailed(errors)
        return [
            OfferOption(
                id=uuid4(),
                product_code=o.product_code,
                variant=o.variant,
                amount=o.amount,
                term_months=o.term_months,
                rate=o.rate,
            )
            for o in options
        ]

    def _option_errors(self, app: Application, prefix: str, option: OfferTerms) -> list[FieldError]:
        try:
            product = self._catalog.get(option.product_code)
        except NotFound:
            return [FieldError(f"{prefix}.product_code", "FORM_COMPLETE", "Такого продукта нет")]
        if not self._catalog.is_allowed(app.purpose, product.code):
            return [
                FieldError(
                    f"{prefix}.product_code",
                    "PURPOSE_ALLOWED",
                    "Этот продукт не подходит под цель заявки",
                )
            ]
        try:
            terms = product.variant(option.variant)
        except ValidationFailed:
            return [
                FieldError(
                    f"{prefix}.variant", "VARIANT_NOT_OFFERED", "У продукта нет такого варианта"
                )
            ]

        errors = []
        amount_check, term_check = product.validate_terms(
            option.variant, option.amount, option.term_months
        )
        for check, name in ((amount_check, "amount"), (term_check, "term_months")):
            if not check.passed:
                errors.append(
                    FieldError(f"{prefix}.{name}", "TERMS_IN_RANGE", f"Допустимо: {check.expected}")
                )
        try:
            self._rates.validate_manual(terms, option.rate)
        except ValidationFailed as exc:
            errors.extend(FieldError(f"{prefix}.rate", e.code, e.message) for e in exc.details)
        return errors


def _list_entry(app: Application) -> AnalystListEntry:
    decision = app.current_decision
    reasons = (
        decision.reasons
        if decision is not None
        and decision.decided_by is None
        and decision.outcome is DecisionOutcome.MANUAL_REVIEW
        else ()
    )
    submitted_at = next(
        (t.at for t in reversed(app.history) if t.to_status is ApplicationStatus.SUBMITTED),
        app.created_at,
    )
    return AnalystListEntry(application=app, manual_reasons=reasons, submitted_at=submitted_at)
