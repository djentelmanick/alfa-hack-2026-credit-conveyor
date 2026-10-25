"""Асинхронный конвейер заявки: CHECKING, SCORING и решение (BACKEND_SPEC, разделы 6, 7, 11)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from app.application.log_context import bind_application
from app.application.source_payload import to_payload
from app.domain.checks import CheckingData, PipelineContext, StatementVerifier, StopFactorRule
from app.domain.decision import DecisionEngine
from app.domain.entities import (
    Application,
    BankStatement,
    CheckResult,
    Consent,
    Decision,
    SourceSnapshot,
)
from app.domain.enums import (
    ApplicationStatus,
    CheckOutcome,
    CheckStage,
    CompanyKind,
    ConsentType,
    DecisionOutcome,
    FetchStatus,
    PipelineStep,
    SourceType,
)
from app.domain.errors import NotFound, ScoringNotImplemented, SourceUnavailable
from app.domain.events import PipelineStepEvent
from app.domain.ports import (
    AccountTurnoverPort,
    BailiffPort,
    CbrKycPort,
    Clock,
    CompanyRegistryPort,
    CreditBureauPort,
    PipelineLock,
    RosfinmonitoringPort,
    StatusPublisher,
    TaxServicePort,
    UnitOfWork,
)
from app.domain.scoring import ScoringModel
from app.domain.value_objects import CreditReport

logger = logging.getLogger(__name__)

S = ApplicationStatus

SCORING_NOT_IMPLEMENTED = "SCORING_NOT_IMPLEMENTED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
PIPELINE_FAILED = "PIPELINE_FAILED"
PIPELINE_STARTED = "PIPELINE_STARTED"
CHECKS_PASSED = "CHECKS_PASSED"
STOP_FACTOR_RULE = "STOP_FACTOR"
STATEMENT_ITEM = "STATEMENT"

STEP_LABELS: Mapping[PipelineStep, str] = {
    PipelineStep.EGRUL: "Проверяем данные в ЕГРЮЛ",
    PipelineStep.BKI: "Проверяем кредитную историю",
    PipelineStep.TURNOVER: "Смотрим обороты по счёту",
    PipelineStep.FNS: "Проверяем налоговые данные",
    PipelineStep.FSSP: "Проверяем исполнительные производства",
    PipelineStep.CBR_KYC: "Проверяем уровень риска в ЦБ",
    PipelineStep.ROSFIN: "Проверяем перечень Росфинмониторинга",
    PipelineStep.STATEMENT: "Проверяем выписку",
    PipelineStep.SCORING: "Считаем скоринг",
}

STATEMENT_INFO_MESSAGE = (
    "Загрузите выписку за последние 6–12 месяцев, последняя операция не старше месяца"
)

# Статусы, в которых заявка ждёт конвейер: зависшие в них заявки PipelineReconciler ставит заново.
PIPELINE_STATUSES = frozenset({S.SUBMITTED, S.CHECKING, S.SCORING})

# Сколько ждать блокировку заявки: дольше, чем прогон дописывает события последнего шага.
LOCK_ATTEMPTS = 10
LOCK_RETRY_SEC = 1.0

_OUTCOME_STATUS: Mapping[DecisionOutcome, ApplicationStatus] = {
    DecisionOutcome.APPROVED: S.APPROVED,
    DecisionOutcome.COUNTER_OFFER: S.COUNTER_OFFER,
    DecisionOutcome.MANUAL_REVIEW: S.MANUAL_REVIEW,
    DecisionOutcome.REJECTED: S.REJECTED,
}

type StepCallback = Callable[[PipelineStep], Awaitable[None]]


@dataclass(frozen=True, kw_only=True, slots=True)
class SourcePorts:
    registry: CompanyRegistryPort
    credit_bureau: CreditBureauPort
    turnover: AccountTurnoverPort
    tax: TaxServicePort
    bailiff: BailiffPort
    cbr: CbrKycPort
    rosfin: RosfinmonitoringPort


@dataclass(frozen=True, kw_only=True, slots=True)
class CollectedData:
    """data есть всегда, context только если ответили все источники."""

    data: CheckingData
    snapshots: tuple[SourceSnapshot, ...]
    context: PipelineContext | None = None

    def missing(self) -> list[SourceType]:
        """Не ответившие и не запрошенные источники в порядке опроса."""
        return list(
            dict.fromkeys(s.source for s in self.snapshots if s.status is not FetchStatus.OK)
        )


SKIPPED_NO_REGISTRY = "Не запрашивали: нет ответа ЕГРЮЛ"


class _Fetcher:
    """Запрашивает источник и записывает снимок ответа. Сбой не бросается, а запоминается."""

    def __init__(self, clock: Clock, attempts: int) -> None:
        self._clock = clock
        self._attempts = attempts
        self.snapshots: list[SourceSnapshot] = []

    async def fetch[T](self, source: SourceType, call: Awaitable[T]) -> T | None:
        try:
            value = await call
        except SourceUnavailable as exc:
            logger.warning("Источник %s не ответил: %s", source, exc.message)
            error = f"Нет ответа после {self._attempts} попыток"
            self._record(source, FetchStatus.FAILED, self._attempts, {"error": error})
            return None
        except NotFound as exc:
            # NotFound тоже «данных нет»: форма уже проверила ИНН, значит реестр ответил иначе.
            logger.warning("Источник %s не дал данных: %s", source, exc.message)
            self._record(source, FetchStatus.FAILED, self._attempts, {"error": exc.message})
            return None
        # Обёртка с повторами не сообщает, с какой попытки пришёл ответ: важен сам факт ответа.
        self._record(source, FetchStatus.OK, 1, to_payload(value))
        return value

    def skip(self, source: SourceType) -> None:
        self._record(source, FetchStatus.SKIPPED, 0, {"error": SKIPPED_NO_REGISTRY})

    def _record(
        self, source: SourceType, status: FetchStatus, attempts: int, payload: Mapping[str, object]
    ) -> None:
        self.snapshots.append(
            SourceSnapshot(
                source=source,
                status=status,
                attempts=attempts,
                payload=payload,
                fetched_at=self._clock.now(),
            )
        )


class DataCollector:
    TURNOVER_MONTHS = 12

    def __init__(
        self,
        ports: SourcePorts,
        verifier: StatementVerifier,
        clock: Clock,
        *,
        retry_attempts: int,
    ) -> None:
        self._ports = ports
        self._verifier = verifier
        self._clock = clock
        self._retry_attempts = retry_attempts

    async def collect(self, app: Application, on_step: StepCallback) -> CollectedData:
        p = self._ports
        fetcher = _Fetcher(self._clock, self._retry_attempts)
        inn = app.inn
        # Форма бизнеса известна из заявки: без ответа ЕГРЮЛ по ней решаем, нужны ли ГИР БО
        # и запросы по директору.
        is_llc = app.company_kind is CompanyKind.LLC

        await on_step(PipelineStep.EGRUL)
        company = await fetcher.fetch(SourceType.EGRUL, p.registry.find_by_inn(inn))
        company_consent, personal_consent = _bki_consents(app)

        await on_step(PipelineStep.BKI)
        credit_report = await fetcher.fetch(
            SourceType.BKI, p.credit_bureau.report(inn, company_consent)
        )
        related: list[CreditReport] = []
        if company is not None:
            persons = sorted({company.director_inn, *(f.inn for f in company.founders)} - {inn})
            for person in persons:
                report = await fetcher.fetch(
                    SourceType.BKI, p.credit_bureau.report(person, personal_consent)
                )
                if report is not None:
                    related.append(report)
        elif is_llc:
            # Без ЕГРЮЛ неизвестны директор и учредители.
            fetcher.skip(SourceType.BKI)

        await on_step(PipelineStep.TURNOVER)
        # None здесь законный ответ «счёта у нас нет», сбой виден только по снимку.
        turnover = await fetcher.fetch(
            SourceType.TURNOVER, p.turnover.monthly_turnover(inn, self.TURNOVER_MONTHS)
        )

        await on_step(PipelineStep.FNS)
        tax = await fetcher.fetch(SourceType.FNS, p.tax.tax_info(inn))
        reported_revenue = None
        if is_llc:
            last_year = self._clock.today().year - 1
            reported_revenue = await fetcher.fetch(
                SourceType.GIR_BO, p.tax.annual_revenue(inn, last_year)
            )

        await on_step(PipelineStep.FSSP)
        bailiff = await fetcher.fetch(SourceType.FSSP, p.bailiff.open_proceedings(inn))
        director_bailiff = None
        if is_llc and company is not None:
            director_bailiff = await fetcher.fetch(
                SourceType.FSSP, p.bailiff.open_proceedings(company.director_inn)
            )
        elif is_llc:
            fetcher.skip(SourceType.FSSP)

        await on_step(PipelineStep.CBR_KYC)
        cbr_level = await fetcher.fetch(SourceType.CBR_KYC, p.cbr.risk_level(inn))

        # Шаг публикуется и без запроса: клиент видит тот же порядок шагов.
        await on_step(PipelineStep.ROSFIN)
        rosfin_hit = None
        if company is not None:
            names = [company.director, *(f.full_name for f in company.founders)]
            rosfin_hit = await fetcher.fetch(SourceType.ROSFIN, p.rosfin.is_listed(inn, names))
        else:
            # Перечень ищет по ФИО: проверка по одному ИНН дала бы ложное «не найдены».
            fetcher.skip(SourceType.ROSFIN)

        statement = _latest_statement(app.statements)
        if statement is not None:
            await on_step(PipelineStep.STATEMENT)

        snapshots = tuple(fetcher.snapshots)
        data = CheckingData(
            application=app,
            company=company,
            credit_report=credit_report,
            related_credit_reports=tuple(related),
            turnover=turnover,
            statement=statement,
            tax=tax,
            bailiff=bailiff,
            director_bailiff=director_bailiff,
            cbr_level=cbr_level,
            rosfin_hit=rosfin_hit,
            reported_annual_revenue=reported_revenue,
            missing=frozenset(s.source for s in snapshots if s.status is not FetchStatus.OK),
            now=self._clock.now(),
        )
        return CollectedData(data=data, snapshots=snapshots, context=self._context(data))

    def _context(self, data: CheckingData) -> PipelineContext | None:
        """Полный контекст для выписки, скоринга и решения, только если ответили все источники."""
        if data.missing:
            return None
        if (
            data.company is None
            or data.credit_report is None
            or data.tax is None
            or data.bailiff is None
            or data.cbr_level is None
            or data.rosfin_hit is None
        ):
            raise ValueError("Все источники ответили, но данных в CheckingData не хватает")
        ctx = PipelineContext(
            application=data.application,
            company=data.company,
            credit_report=data.credit_report,
            related_credit_reports=data.related_credit_reports,
            turnover=data.turnover,
            statement=data.statement,
            tax=data.tax,
            bailiff=data.bailiff,
            director_bailiff=data.director_bailiff,
            cbr_level=data.cbr_level,
            rosfin_hit=data.rosfin_hit,
            reported_annual_revenue=data.reported_annual_revenue,
            now=data.now,
        )
        if ctx.statement is not None:
            ctx = replace(ctx, statement_verification=self._verifier.verify(ctx.statement, ctx))
        return ctx


def _bki_consents(app: Application) -> tuple[Consent, Consent]:
    """Согласие для запроса по компании и для запросов по физлицам.

    Форма уже проверила, что нужные согласия есть. У ИП согласие одно, оно и для физлица.
    """
    by_type = {c.type: c for c in app.consents}
    company = by_type.get(ConsentType.BKI_COMPANY) or by_type.get(ConsentType.BKI_PERSONAL)
    personal = by_type.get(ConsentType.BKI_PERSONAL) or company
    if company is None or personal is None:
        raise ValueError(f"У заявки {app.id} нет согласия на запрос в БКИ")
    return company, personal


def _latest_statement(statements: Sequence[BankStatement]) -> BankStatement | None:
    # По порядку загрузки, а не по uploaded_at: у двух загрузок время может совпасть.
    return statements[-1] if statements else None


class CheckingKind(StrEnum):
    OK = "OK"
    STOP = "STOP"
    NEEDS_INFO = "NEEDS_INFO"
    SOURCE_DOWN = "SOURCE_DOWN"


@dataclass(frozen=True, kw_only=True, slots=True)
class CheckingOutcome:
    kind: CheckingKind
    checks: tuple[CheckResult, ...] = ()
    info_items: tuple[str, ...] = ()
    stop_code: str | None = None


class PipelineOrchestrator:
    """Каждый шаг это отдельная транзакция: transition, commit, затем publish."""

    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        collector: DataCollector,
        stop_rules: Sequence[StopFactorRule],
        verifier: StatementVerifier,
        scoring: ScoringModel,
        decision: DecisionEngine,
        publisher: StatusPublisher,
        lock: PipelineLock,
        clock: Clock,
        offer_ttl: timedelta,
    ) -> None:
        self._uow = uow
        self._lock = lock
        self._collector = collector
        self._stop_rules = tuple(stop_rules)
        self._verifier = verifier
        self._scoring = scoring
        self._decision = decision
        self._publisher = publisher
        self._clock = clock
        self._offer_ttl = offer_ttl

    async def run(self, application_id: UUID, run_id: UUID) -> None:
        """Идемпотентен: повторная доставка и задача устаревшего прогона ничего не дублируют.

        Бросает NotFound, если заявки нет.
        """
        with bind_application(application_id):
            for _ in range(LOCK_ATTEMPTS):
                async with self._lock.hold(application_id) as acquired:
                    if acquired:
                        await self._run(application_id, run_id)
                        return
                # Блокировку может держать прогон, который уже закоммитил итог и дописывает
                # события, а эта задача от нового прогона после recheck или повторной отправки.
                # Сразу подтвердить её значило бы потерять прогон до reconciler.
                await asyncio.sleep(LOCK_RETRY_SEC)
            logger.info("Заявку %s ведёт другой обработчик, задача пропущена", application_id)

    async def give_up(self, application_id: UUID, run_id: UUID) -> None:
        """Прогон падает при каждой доставке: отдаём заявку аналитику, а не повторяем бесконечно."""
        with bind_application(application_id):
            async with self._lock.hold(application_id) as acquired:
                if not acquired:
                    return

                def change(app: Application) -> None:
                    if app.status not in PIPELINE_STATUSES or app.pipeline_run_id != run_id:
                        return
                    now = self._clock.now()
                    if app.status is S.SUBMITTED:
                        app.transition_to(
                            S.CHECKING, actor_id=None, reason=PIPELINE_STARTED, now=now
                        )
                    app.add_decision(
                        _system_decision(
                            "MANUAL_REVIEW", DecisionOutcome.MANUAL_REVIEW, (PIPELINE_FAILED,), now
                        )
                    )
                    app.transition_to(
                        S.MANUAL_REVIEW, actor_id=None, reason=PIPELINE_FAILED, now=now
                    )

                await self._step(application_id, change)
                logger.error("Заявка %s передана аналитику после сбоев конвейера", application_id)

    async def _run(self, application_id: UUID, run_id: UUID) -> None:
        async with self._uow() as uow:
            current = await uow.applications.get(application_id)
        status = current.status
        if status not in PIPELINE_STATUSES:
            logger.info("Заявка %s в статусе %s, конвейер не нужен", application_id, status)
            return
        if current.pipeline_run_id != run_id:
            logger.info(
                "Задача устаревшего прогона %s для заявки %s пропущена", run_id, application_id
            )
            return

        if status is S.SUBMITTED:
            app = await self._step(application_id, self._to(S.CHECKING, PIPELINE_STARTED))
        else:
            # Прогон прервался посреди конвейера: собираем заново,
            # снимки и проверки этапа заменятся целиком.
            app = current

        collected = await self._collector.collect(app, self._step_publisher(app))
        outcome = self.checking(collected)
        await self._step(application_id, self._apply_checking(collected, outcome))
        if outcome.kind is not CheckingKind.OK or collected.context is None:
            return
        await self._score_and_decide(collected.context)

    def checking(self, collected: CollectedData) -> CheckingOutcome:
        # Стоп-факторы считаются всегда: аналитик видит их и при неполных данных.
        checks = [rule.check(collected.data) for rule in self._stop_rules]
        ctx = collected.context
        if ctx is None:
            missing = [
                CheckResult(
                    code=f"SOURCE_{source}",
                    stage=CheckStage.CHECKING,
                    expected="источник ответил",
                    actual="нет данных",
                    outcome=CheckOutcome.NO_DATA,
                )
                for source in collected.missing()
            ]
            # Автоотказа нет даже при сработавшем стоп-факторе: решает аналитик (SCOPE, раздел 4).
            return CheckingOutcome(kind=CheckingKind.SOURCE_DOWN, checks=(*checks, *missing))

        stop = next((c for c in checks if c.outcome is CheckOutcome.STOP_FACTOR), None)
        if stop is not None:
            return CheckingOutcome(
                kind=CheckingKind.STOP, checks=tuple(checks), stop_code=stop.code
            )

        if ctx.statement is not None:
            period = self._verifier.check_period(ctx.statement, ctx.today)
            checks.append(period)
            verification = ctx.statement_verification
            if verification is not None:
                checks.extend(verification.checks)
            if not period.passed:
                return CheckingOutcome(
                    kind=CheckingKind.NEEDS_INFO, checks=tuple(checks), info_items=(STATEMENT_ITEM,)
                )
        return CheckingOutcome(kind=CheckingKind.OK, checks=tuple(checks))

    def _apply_checking(
        self, collected: CollectedData, outcome: CheckingOutcome
    ) -> Callable[[Application], None]:
        def change(app: Application) -> None:
            now = self._clock.now()
            app.record_snapshots(collected.snapshots)
            app.record_checks(CheckStage.CHECKING, outcome.checks)
            match outcome.kind:
                case CheckingKind.SOURCE_DOWN:
                    # Решение нужно, чтобы очередь аналитика показала причину ручной проверки.
                    app.add_decision(
                        _system_decision(
                            "MANUAL_REVIEW",
                            DecisionOutcome.MANUAL_REVIEW,
                            (SOURCE_UNAVAILABLE,),
                            now,
                        )
                    )
                    app.transition_to(
                        S.MANUAL_REVIEW, actor_id=None, reason=SOURCE_UNAVAILABLE, now=now
                    )
                case CheckingKind.STOP:
                    code = outcome.stop_code or STOP_FACTOR_RULE
                    app.add_decision(
                        _system_decision(STOP_FACTOR_RULE, DecisionOutcome.REJECTED, (code,), now)
                    )
                    app.transition_to(S.REJECTED, actor_id=None, reason=code, now=now)
                case CheckingKind.NEEDS_INFO if app.status is S.SCORING:
                    # Перезапуск после падения в SCORING: дозапрос оттуда запрещён таблицей
                    # переходов, а выписка за это время устарела. Решает аналитик.
                    app.transition_to(
                        S.MANUAL_REVIEW,
                        actor_id=None,
                        reason=StatementVerifier.PERIOD_CODE,
                        now=now,
                    )
                case CheckingKind.NEEDS_INFO:
                    app.request_info(
                        outcome.info_items,
                        STATEMENT_INFO_MESSAGE,
                        actor_id=None,
                        now=now,
                        reason=StatementVerifier.PERIOD_CODE,
                    )
                case CheckingKind.OK if app.status is not S.SCORING:
                    app.transition_to(S.SCORING, actor_id=None, reason=CHECKS_PASSED, now=now)
                case CheckingKind.OK:
                    pass

        return change

    async def _score_and_decide(self, ctx: PipelineContext) -> None:
        application_id = ctx.application.id
        try:
            scoring = self._scoring.score(ctx)
        except ScoringNotImplemented:
            # Модели пока нет (BACKEND_SPEC, раздел 7): решение принимает аналитик.
            def to_manual(app: Application) -> None:
                now = self._clock.now()
                app.add_decision(
                    _system_decision(
                        "MANUAL_REVIEW",
                        DecisionOutcome.MANUAL_REVIEW,
                        (SCORING_NOT_IMPLEMENTED,),
                        now,
                    )
                )
                app.transition_to(
                    S.MANUAL_REVIEW, actor_id=None, reason=SCORING_NOT_IMPLEMENTED, now=now
                )

            await self._step(application_id, to_manual)
            return

        decision = self._decision.decide(ctx, scoring)
        target = _OUTCOME_STATUS.get(decision.outcome)
        if target is None:
            raise ValueError(f"Решение {decision.outcome} недопустимо после скоринга")

        def apply(app: Application) -> None:
            app.record_scoring(scoring)
            app.add_decision(decision)
            app.transition_to(
                target,
                actor_id=None,
                reason=", ".join(decision.reasons) or decision.rule_code,
                now=self._clock.now(),
                offer_ttl=self._offer_ttl,
            )

        await self._step(application_id, apply)

    async def _step(
        self, application_id: UUID, change: Callable[[Application], None]
    ) -> Application:
        async with self._uow() as uow:
            app = await uow.applications.get(application_id)
            change(app)
            await uow.commit()
        for event in app.pull_events():
            await self._publisher.publish(event)
        return app

    def _to(self, target: ApplicationStatus, reason: str) -> Callable[[Application], None]:
        def change(app: Application) -> None:
            app.transition_to(target, actor_id=None, reason=reason, now=self._clock.now())

        return change

    def _step_publisher(self, app: Application) -> StepCallback:
        async def publish(step: PipelineStep) -> None:
            await self._publisher.publish(
                PipelineStepEvent(
                    application_id=app.id,
                    applicant_id=app.applicant_id,
                    step=step,
                    label=STEP_LABELS[step],
                    at=self._clock.now(),
                )
            )

        return publish


def _system_decision(
    rule_code: str, outcome: DecisionOutcome, reasons: tuple[str, ...], now: datetime
) -> Decision:
    return Decision(
        id=uuid4(),
        rule_code=rule_code,
        outcome=outcome,
        rate=None,
        reasons=reasons,
        decided_by=None,
        comment=None,
        decided_at=now,
    )
