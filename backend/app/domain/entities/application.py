import secrets
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

from app.domain.entities.parts import (
    ApplicationForm,
    BankStatement,
    CheckResult,
    Consent,
    Decision,
    InfoRequest,
    OfferOption,
    SigningCode,
    SourceSnapshot,
    StatusTransition,
)
from app.domain.enums import (
    ApplicationStatus,
    CheckStage,
    CompanyKind,
    DecisionOutcome,
    FetchStatus,
    Purpose,
    SourceType,
    Variant,
)
from app.domain.errors import (
    BadSigningCode,
    FieldError,
    InvalidStatus,
    NotFound,
    OfferExpired,
    SourcesNotReady,
    ValidationFailed,
)
from app.domain.events import StatusChangedEvent
from app.domain.scoring.result import ScoringResult
from app.domain.state_machine import (
    EDITABLE_STATUSES,
    OFFER_STATUSES,
    ApplicationStateMachine,
)
from app.domain.value_objects import Collateral, Money

if TYPE_CHECKING:
    # Продукты сами импортируют сущности договоров, поэтому прямой импорт дал бы цикл.
    from app.domain.products.loan_product import LoanProduct

S = ApplicationStatus

ANALYST_RULE_CODE = "ANALYST_DECISION"
ANALYST_INFO_MESSAGE = "Банку нужны дополнительные данные по заявке"


class Application:
    """Агрегат заявки. Статус меняется только через transition_to и методы-сценарии."""

    def __init__(
        self,
        *,
        id: UUID,
        applicant_id: UUID,
        inn: str,
        company_kind: CompanyKind,
        company_name: str = "",
        okved: str = "",
        purpose: Purpose,
        product_code: str,
        variant: Variant,
        amount: Money,
        term_months: int,
        owner_guarantee: bool = False,
        collateral: Collateral | None = None,
        status: ApplicationStatus = S.DRAFT,
        created_at: datetime,
        updated_at: datetime | None = None,
        expires_at: datetime | None = None,
        accepted_offer_id: UUID | None = None,
        contract_id: UUID | None = None,
        pipeline_run_id: UUID | None = None,
        signing_code: SigningCode | None = None,
        consents: Iterable[Consent] = (),
        statements: Iterable[BankStatement] = (),
        snapshots: Iterable[SourceSnapshot] = (),
        checks: Iterable[CheckResult] = (),
        scoring: ScoringResult | None = None,
        decisions: Iterable[Decision] = (),
        info_requests: Iterable[InfoRequest] = (),
        history: Iterable[StatusTransition] = (),
    ) -> None:
        self.id = id
        self.applicant_id = applicant_id
        self.inn = inn
        self.company_kind = company_kind
        # Запоминаются из ЕГРЮЛ при создании: карточка заявки не должна ждать ответа реестра.
        self.company_name = company_name
        self.okved = okved
        self.purpose = purpose
        self.product_code = product_code
        self.variant = variant
        self.amount = amount
        self.term_months = term_months
        self.owner_guarantee = owner_guarantee
        self.collateral = collateral
        self._status = status
        self.created_at = created_at
        self.updated_at = updated_at or created_at
        self.expires_at = expires_at
        self.accepted_offer_id = accepted_offer_id
        self.contract_id = contract_id
        self.pipeline_run_id = pipeline_run_id
        self.signing_code = signing_code
        self.consents = list(consents)
        self.statements = list(statements)
        self.snapshots = list(snapshots)
        self.checks = list(checks)
        self.scoring = scoring
        self.decisions = list(decisions)
        self.info_requests = list(info_requests)
        self.history = list(history)
        self._events: list[StatusChangedEvent] = []

    @classmethod
    def create_draft(
        cls,
        *,
        id: UUID,
        applicant_id: UUID,
        inn: str,
        company_kind: CompanyKind,
        form: ApplicationForm,
        product: LoanProduct,
        now: datetime,
        company_name: str = "",
        okved: str = "",
        consents: Iterable[Consent] = (),
    ) -> Self:
        _ensure_collateral_fits(form, product)
        return cls(
            id=id,
            applicant_id=applicant_id,
            inn=inn,
            company_kind=company_kind,
            company_name=company_name,
            okved=okved,
            consents=consents,
            purpose=form.purpose,
            product_code=form.product_code,
            variant=form.variant,
            amount=form.amount,
            term_months=form.term_months,
            owner_guarantee=form.owner_guarantee,
            collateral=form.collateral,
            created_at=now,
        )

    @property
    def status(self) -> ApplicationStatus:
        return self._status

    @property
    def form(self) -> ApplicationForm:
        return ApplicationForm(
            purpose=self.purpose,
            product_code=self.product_code,
            variant=self.variant,
            amount=self.amount,
            term_months=self.term_months,
            owner_guarantee=self.owner_guarantee,
            collateral=self.collateral,
        )

    @property
    def current_decision(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def open_info_request(self) -> InfoRequest | None:
        last = self.info_requests[-1] if self.info_requests else None
        return last if last is not None and last.resolved_at is None else None

    def is_active(self) -> bool:
        return ApplicationStateMachine.is_active(self._status)

    def update_form(self, form: ApplicationForm, product: LoanProduct, *, now: datetime) -> None:
        self.ensure_editable()
        _ensure_collateral_fits(form, product)
        self._apply_form(form)
        self.updated_at = now

    def replace_consents(self, consents: Iterable[Consent]) -> None:
        """Согласия можно дать и после создания черновика: перед отправкой они обновляются."""
        self.ensure_editable()
        self.consents = list(consents)

    def statement(self, statement_id: UUID) -> BankStatement:
        for statement in self.statements:
            if statement.id == statement_id:
                return statement
        raise NotFound("Выписка не найдена")

    def attach_statement(self, statement: BankStatement, *, now: datetime) -> None:
        self.ensure_editable()
        self.statements.append(statement)
        self.updated_at = now

    def remove_statement(self, statement_id: UUID, *, now: datetime) -> BankStatement:
        """Удалённая выписка нужна вызывающему, чтобы убрать её файл из хранилища."""
        self.ensure_editable()
        statement = self.statement(statement_id)
        self.statements.remove(statement)
        self.updated_at = now
        return statement

    def record_snapshots(self, snapshots: Iterable[SourceSnapshot]) -> None:
        """Повторная проверка заменяет ответы источников: в отчёте нужен последний прогон."""
        self.snapshots = list(snapshots)

    def record_checks(self, stage: CheckStage, checks: Iterable[CheckResult]) -> None:
        self.checks = [c for c in self.checks if c.stage is not stage]
        self.checks.extend(checks)

    def record_scoring(self, scoring: ScoringResult) -> None:
        self.scoring = scoring

    def add_decision(self, decision: Decision) -> None:
        self.decisions.append(decision)

    def submit(self, product: LoanProduct, *, actor_id: UUID, now: datetime) -> None:
        self.ensure_editable()
        _ensure_ready_to_submit(self.form, product, self.company_kind)
        if (request := self.open_info_request) is not None:
            request.resolved_at = now
        self.transition_to(S.SUBMITTED, actor_id=actor_id, reason="Заявка отправлена", now=now)
        self.pipeline_run_id = uuid4()

    def request_info(
        self,
        items: Sequence[str],
        message: str,
        *,
        actor_id: UUID | None,
        now: datetime,
        reason: str | None = None,
    ) -> None:
        """reason это код причины в истории и событии; без него туда попадает текст для клиента."""
        if not items:
            raise ValidationFailed(
                [FieldError("items", "REQUIRED", "Укажите, каких данных не хватает")]
            )
        self.transition_to(S.NEEDS_INFO, actor_id=actor_id, reason=reason or message, now=now)
        self.info_requests.append(
            InfoRequest(items=tuple(items), message=message, created_by=actor_id, created_at=now)
        )

    def accept_offer(self, option_id: UUID | None, *, actor_id: UUID, now: datetime) -> None:
        if self._status not in OFFER_STATUSES:
            raise InvalidStatus("Принять можно только одобрение или контрпредложение")
        if self.expires_at is not None and now >= self.expires_at:
            raise OfferExpired()
        if self._status is S.APPROVED:
            self.transition_to(S.SIGNING, actor_id=actor_id, reason="Условия приняты", now=now)
            return

        if option_id is None:
            raise ValidationFailed(
                [FieldError("offer_id", "REQUIRED", "Выберите вариант контрпредложения")]
            )
        decision = self.current_decision
        option = decision.offer(option_id) if decision is not None else None
        if option is None:
            raise NotFound("Вариант контрпредложения не найден")

        self.product_code = option.product_code
        self.variant = option.variant
        self.amount = option.amount
        self.term_months = option.term_months
        self.accepted_offer_id = option.id
        if option.variant is Variant.SECURED:
            self.request_info(
                ["COLLATERAL"],
                "Заполните сведения о залоге",
                actor_id=actor_id,
                now=now,
            )
        else:
            self.collateral = None
            self.transition_to(
                S.SIGNING, actor_id=actor_id, reason="Контрпредложение принято", now=now
            )

    def agreed_rate(self) -> Decimal:
        """Ставка договора: из выбранного варианта контрпредложения, иначе из решения."""
        decision = self.current_decision
        if decision is None:
            raise InvalidStatus("По заявке нет решения")
        if self.accepted_offer_id is not None:
            option = decision.offer(self.accepted_offer_id)
            if option is not None and option.rate is not None:
                return option.rate
        if decision.rate is None:
            raise ValueError(f"В решении по заявке {self.id} нет ставки")
        return decision.rate

    def issue_signing_code(self, code: SigningCode) -> None:
        """Новый код заменяет прежний: клиент мог не успеть ввести старый."""
        self._ensure_signing()
        self.signing_code = code

    def verify_signing_code(self, code: str, *, now: datetime) -> None:
        self._ensure_signing()
        current = self.signing_code
        if (
            current is None
            or current.is_expired(now)
            or not secrets.compare_digest(current.code, code)
        ):
            raise BadSigningCode()

    def issue(self, contract_id: UUID, *, actor_id: UUID, now: datetime) -> None:
        self._ensure_signing()
        self.contract_id = contract_id
        self.transition_to(S.ISSUED, actor_id=actor_id, reason="Договор подписан", now=now)

    def _ensure_signing(self) -> None:
        if self._status is not S.SIGNING:
            raise InvalidStatus("Подписание доступно только после принятия условий")

    def missing_sources(self) -> list[SourceType]:
        """Источники, которые не ответили или не запрашивались (FAILED или SKIPPED)."""
        return list(
            dict.fromkeys(s.source for s in self.snapshots if s.status is not FetchStatus.OK)
        )

    def ensure_sources_ready(self) -> None:
        if not self.snapshots or self.missing_sources():
            raise SourcesNotReady()

    def approve_by_analyst(
        self,
        rate: Decimal,
        *,
        actor_id: UUID,
        comment: str,
        now: datetime,
        offer_ttl: timedelta,
    ) -> None:
        """Ставка уже проверена по диапазону варианта: каталог продуктов знает сервис."""
        self._ensure_manual_review(comment)
        # Одобрение без ответа источника пропустило бы непроверенный стоп-фактор (SCOPE, раздел 4).
        self.ensure_sources_ready()
        self._add_analyst_decision(DecisionOutcome.APPROVED, actor_id, comment, now, rate=rate)
        self.transition_to(
            S.APPROVED,
            actor_id=actor_id,
            reason="Одобрено аналитиком",
            now=now,
            offer_ttl=offer_ttl,
        )

    def counter_offer_by_analyst(
        self,
        offers: Sequence[OfferOption],
        *,
        actor_id: UUID,
        comment: str,
        now: datetime,
        offer_ttl: timedelta,
    ) -> None:
        self._ensure_manual_review(comment)
        if not offers:
            raise ValidationFailed(
                [FieldError("options", "REQUIRED", "Добавьте хотя бы один вариант")]
            )
        self._add_analyst_decision(
            DecisionOutcome.COUNTER_OFFER,
            actor_id,
            comment,
            now,
            rate=offers[0].rate,
            offers=tuple(offers),
        )
        self.transition_to(
            S.COUNTER_OFFER,
            actor_id=actor_id,
            reason="Аналитик предложил другие условия",
            now=now,
            offer_ttl=offer_ttl,
        )

    def request_info_by_analyst(
        self, items: Mapping[str, str], *, actor_id: UUID, comment: str, now: datetime
    ) -> None:
        """items: код пункта и пояснение для клиента."""
        self._ensure_manual_review(comment)
        self._add_analyst_decision(DecisionOutcome.NEEDS_INFO, actor_id, comment, now)
        self.request_info(
            list(items),
            ANALYST_INFO_MESSAGE,
            actor_id=actor_id,
            now=now,
            reason="Аналитик запросил данные",
        )
        self.info_requests[-1].item_messages = dict(items)

    def reject_by_analyst(
        self, reason_code: str, *, actor_id: UUID, comment: str, now: datetime
    ) -> None:
        self._ensure_manual_review(comment)
        self._add_analyst_decision(
            DecisionOutcome.REJECTED, actor_id, comment, now, reasons=(reason_code,)
        )
        self.transition_to(
            S.REJECTED, actor_id=actor_id, reason="Отказ по решению аналитика", now=now
        )

    def start_recheck(self, *, actor_id: UUID, comment: str, now: datetime) -> None:
        self._ensure_manual_review(comment)
        self.transition_to(
            S.CHECKING, actor_id=actor_id, reason="Повторная проверка источников", now=now
        )
        # Новый прогон: задачи прежнего, ещё лежащие в очереди, worker пропустит.
        self.pipeline_run_id = uuid4()

    def _ensure_manual_review(self, comment: str) -> None:
        if not comment.strip():
            raise ValidationFailed(
                [FieldError("comment", "REQUIRED", "Комментарий к решению обязателен")]
            )
        # Таблица переходов этого не ловит: в NEEDS_INFO и REJECTED можно попасть и из CHECKING.
        if self._status is not S.MANUAL_REVIEW:
            raise InvalidStatus("Решение аналитика доступно только на ручной проверке")

    def _add_analyst_decision(
        self,
        outcome: DecisionOutcome,
        actor_id: UUID,
        comment: str,
        now: datetime,
        *,
        rate: Decimal | None = None,
        reasons: tuple[str, ...] = (),
        offers: tuple[OfferOption, ...] = (),
    ) -> None:
        self.add_decision(
            Decision(
                id=uuid4(),
                rule_code=ANALYST_RULE_CODE,
                outcome=outcome,
                rate=rate,
                reasons=reasons,
                offers=offers,
                decided_by=actor_id,
                comment=comment.strip(),
                decided_at=now,
            )
        )

    def cancel(self, *, actor_id: UUID | None, reason: str, now: datetime) -> None:
        self.transition_to(S.CANCELLED, actor_id=actor_id, reason=reason, now=now)

    def transition_to(
        self,
        target: ApplicationStatus,
        *,
        actor_id: UUID | None,
        reason: str,
        now: datetime,
        offer_ttl: timedelta | None = None,
    ) -> None:
        source = self._status
        ApplicationStateMachine.transition(source, target)
        if target in OFFER_STATUSES:
            if offer_ttl is None:
                raise ValueError(f"Для перехода в {target} нужен offer_ttl")
            self.expires_at = now + offer_ttl
        else:
            self.expires_at = None
        if target is not S.SIGNING:
            self.signing_code = None

        self._status = target
        self.updated_at = now
        self.history.append(
            StatusTransition(
                from_status=source, to_status=target, actor_id=actor_id, reason=reason, at=now
            )
        )
        self._events.append(
            StatusChangedEvent(
                application_id=self.id,
                applicant_id=self.applicant_id,
                from_status=source,
                to_status=target,
                reason=reason,
                at=now,
                expires_at=self.expires_at,
            )
        )

    def pull_events(self) -> list[StatusChangedEvent]:
        events, self._events = self._events, []
        return events

    def ensure_editable(self) -> None:
        if self._status not in EDITABLE_STATUSES:
            raise InvalidStatus("Анкету можно менять только в черновике или при дозапросе")

    def _apply_form(self, form: ApplicationForm) -> None:
        self.purpose = form.purpose
        self.product_code = form.product_code
        self.variant = form.variant
        self.amount = form.amount
        self.term_months = form.term_months
        self.owner_guarantee = form.owner_guarantee
        self.collateral = form.collateral


def _ensure_product_matches(form: ApplicationForm, product: LoanProduct) -> None:
    if form.product_code != product.code:
        raise ValueError(f"Анкета на {form.product_code}, а передан продукт {product.code}")


def _ensure_collateral_fits(form: ApplicationForm, product: LoanProduct) -> None:
    """То, что должно выполняться уже в черновике: залог можно не указать, но неверный нельзя."""
    _ensure_product_matches(form, product)
    if form.collateral is None:
        return
    if form.variant is Variant.UNSECURED:
        raise ValidationFailed(
            [
                FieldError(
                    "collateral",
                    "COLLATERAL_NOT_ALLOWED",
                    "Залог указывается только в варианте с залогом",
                )
            ]
        )
    min_pct = product.variant(form.variant).collateral_coverage_min_pct
    if min_pct is not None and not form.collateral.covers(form.amount, min_pct):
        raise ValidationFailed(
            [
                FieldError(
                    "collateral.estimated_value",
                    "COLLATERAL_COVERAGE",
                    f"Оценка залога должна покрывать не меньше {min_pct}% суммы",
                )
            ]
        )


def _ensure_ready_to_submit(
    form: ApplicationForm, product: LoanProduct, company_kind: CompanyKind
) -> None:
    _ensure_collateral_fits(form, product)
    if form.variant is Variant.SECURED and form.collateral is None:
        raise ValidationFailed([FieldError("collateral", "COLLATERAL_REQUIRED", "Укажите залог")])
    if (
        company_kind is CompanyKind.LLC
        and form.variant is Variant.UNSECURED
        and not form.owner_guarantee
    ):
        raise ValidationFailed(
            [
                FieldError(
                    "owner_guarantee",
                    "OWNER_GUARANTEE",
                    "Для ООО без залога нужно поручительство собственника",
                )
            ]
        )
