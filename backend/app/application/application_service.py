import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID, uuid4

from app.application.findings_builder import ApplicationFindings, FindingsBuilder
from app.application.form_validator import FormValidator
from app.domain.entities import (
    Application,
    ApplicationForm,
    BankStatement,
    CheckResult,
    Decision,
    StatusTransition,
    TokenClaims,
)
from app.domain.enums import AllowedAction, ApplicationStatus, CheckStage, Purpose, Role, Variant
from app.domain.errors import (
    ActiveApplicationExists,
    FieldError,
    FileTooLarge,
    Forbidden,
    InvalidStatus,
    NotDirector,
    NotFound,
    StorageUnavailable,
    ValidationFailed,
)
from app.domain.ports import (
    Clock,
    CompanyRegistryPort,
    FileStoragePort,
    PipelineTask,
    SignatureVerifierPort,
    StatementParser,
    StatusPublisher,
    TaskQueue,
    UnitOfWork,
)
from app.domain.products import LoanProduct, ProductCatalog
from app.domain.scoring import ScoringResult
from app.domain.state_machine import ApplicationStateMachine
from app.domain.value_objects import Collateral, Money

logger = logging.getLogger(__name__)

CLIENT_CANCEL_REASON = "Отменено клиентом"

# Пока заявка в этих статусах, worker ещё обходит источники: отчёт и findings недоступны.
COLLECTING_STATUSES = frozenset({ApplicationStatus.SUBMITTED, ApplicationStatus.CHECKING})


async def start_pipeline(queue: TaskQueue, app: Application) -> None:
    """Ставит задачу конвейера после commit.

    Ошибку только логируем: заявка уже сохранена, и её подхватит PipelineReconciler.
    """
    if app.pipeline_run_id is None:
        raise ValueError(f"У заявки {app.id} нет pipeline_run_id")
    try:
        await queue.enqueue(PipelineTask(application_id=app.id, run_id=app.pipeline_run_id))
    except Exception:
        logger.exception("Не удалось поставить заявку %s в очередь конвейера", app.id)


class _Unset(Enum):
    UNSET = "UNSET"


UNSET = _Unset.UNSET


@dataclass(frozen=True, kw_only=True, slots=True)
class FormPatch:
    """Изменённые поля анкеты. collateral=None означает «убрать залог», UNSET это «не трогать»."""

    purpose: Purpose | None = None
    product_code: str | None = None
    variant: Variant | None = None
    amount: Money | None = None
    term_months: int | None = None
    owner_guarantee: bool | None = None
    collateral: Collateral | _Unset | None = UNSET

    def apply(self, form: ApplicationForm) -> ApplicationForm:
        return replace(
            form,
            purpose=self.purpose or form.purpose,
            product_code=self.product_code or form.product_code,
            variant=self.variant or form.variant,
            amount=self.amount or form.amount,
            term_months=self.term_months if self.term_months is not None else form.term_months,
            owner_guarantee=(
                self.owner_guarantee if self.owner_guarantee is not None else form.owner_guarantee
            ),
            collateral=form.collateral if self.collateral is UNSET else self.collateral,
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class ClientApplicationView:
    application: Application
    allowed_actions: list[AllowedAction]


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicationPage:
    items: list[Application]
    total: int
    page: int
    size: int


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryEntry:
    transition: StatusTransition
    actor_role: Role | None
    actor_name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DecisionView:
    decision: Decision
    scoring: ScoringResult | None
    expires_at: datetime | None


SYSTEM_ACTOR_NAME = "Система"

STATEMENT_CONTENT_TYPE = "text/plain"
DEFAULT_STATEMENT_NAME = "statement.txt"
# Имя файла и банка приходят от клиента, а в БД под них по 255 символов.
MAX_NAME_LENGTH = 255


@dataclass(frozen=True, kw_only=True, slots=True)
class UploadedFile:
    file_name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, kw_only=True, slots=True)
class PresignedUrl:
    url: str
    file_name: str
    expires_at: datetime
    version_no: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class StatementFiles:
    """Всё, что нужно для приёма выписки: разбор, проверка ЭП банка и хранение оригинала."""

    storage: FileStoragePort
    signatures: SignatureVerifierPort
    parser: StatementParser
    bucket: str
    max_bytes: int
    url_ttl: timedelta

    @staticmethod
    def key(application_id: UUID, statement_id: UUID) -> str:
        return f"statements/{application_id}/{statement_id}.txt"


class ApplicationService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        catalog: ProductCatalog,
        registry: CompanyRegistryPort,
        validator: FormValidator,
        queue: TaskQueue,
        publisher: StatusPublisher,
        clock: Clock,
        findings: FindingsBuilder,
        statements: StatementFiles,
    ) -> None:
        self._uow = uow
        self._findings = findings
        self._statements = statements
        self._catalog = catalog
        self._registry = registry
        self._validator = validator
        self._queue = queue
        self._publisher = publisher
        self._clock = clock

    async def create_draft(
        self, claims: TokenClaims, inn: str, form: ApplicationForm
    ) -> Application:
        product = self._product(form.product_code)
        try:
            company = await self._registry.find_by_inn(inn)
        except NotFound:
            raise ValidationFailed(
                [FieldError("inn", "FORM_COMPLETE", "ИНН не найден в ЕГРЮЛ/ЕГРИП")]
            ) from None
        async with self._uow() as uow:
            if await uow.applications.find_active_by_inn(inn) is not None:
                raise ActiveApplicationExists()
            app = Application.create_draft(
                id=uuid4(),
                applicant_id=claims.user_id,
                inn=inn,
                company_kind=company.kind,
                company_name=company.name,
                okved=company.okved,
                form=form,
                product=product,
                now=self._clock.now(),
                consents=await uow.consents.list_for(inn, claims.user_id),
            )
            await uow.applications.add(app)
            await uow.commit()
        logger.info("Создан черновик заявки %s", app.id)
        return app

    async def update_draft(
        self, claims: TokenClaims, application_id: UUID, patch: FormPatch
    ) -> Application:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            form = patch.apply(app.form)
            app.update_form(form, self._product(form.product_code), now=self._clock.now())
            await uow.commit()
        return app

    async def validate(self, claims: TokenClaims, application_id: UUID) -> list[FieldError]:
        """Ошибки формы без отправки: фронт подсвечивает поля до нажатия «Отправить»."""
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            try:
                await self._run_validator(uow, app)
            except ValidationFailed as exc:
                return list(exc.details)
            except (NotDirector, ActiveApplicationExists) as exc:
                return [FieldError("inn", exc.code, exc.message)]
        return []

    @property
    def statement_read_limit(self) -> int:
        """Сколько байт файла читать: на байт больше лимита, чтобы отличить слишком большой файл."""
        return self._statements.max_bytes + 1

    async def attach_statement(
        self, claims: TokenClaims, application_id: UUID, file: UploadedFile
    ) -> BankStatement:
        files = self._statements
        if len(file.data) > files.max_bytes:
            raise FileTooLarge(f"Выписка больше {files.max_bytes // (1024 * 1024)} МБ")
        # Владелец и статус проверяются до разбора: чужой или замороженной заявке файл не нужен.
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            app.ensure_editable()
        parsed = files.parser.parse(file.data)
        parsed = replace(parsed, bank_name=parsed.bank_name[:MAX_NAME_LENGTH])
        if parsed.account_inn != app.inn:
            raise ValidationFailed(
                [FieldError("file", "STATEMENT_INN_MATCH", "Выписка по счёту другого ИНН")]
            )
        signature = await files.signatures.verify(file.data)

        statement_id = uuid4()
        key = files.key(application_id, statement_id)
        await files.storage.put(files.bucket, key, file.data, STATEMENT_CONTENT_TYPE)
        try:
            async with self._uow() as uow:
                app = await self._own(uow, claims, application_id)
                statement = parsed.to_statement(
                    id=statement_id,
                    file_name=_safe_file_name(file.file_name),
                    file_key=key,
                    signature_status=signature,
                    uploaded_at=self._clock.now(),
                )
                app.attach_statement(statement, now=self._clock.now())
                await uow.commit()
        except Exception:
            # Без строки в БД файл никто не найдёт: убираем его, чтобы не копить мусор в S3.
            await self._delete_file(key)
            raise
        logger.info("К заявке %s загружена выписка %s", application_id, statement_id)
        return statement

    async def delete_statement(
        self, claims: TokenClaims, application_id: UUID, statement_id: UUID
    ) -> None:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            statement = app.remove_statement(statement_id, now=self._clock.now())
            await uow.commit()
        await self._delete_file(statement.file_key)

    async def statement_url(
        self, claims: TokenClaims, application_id: UUID, statement_id: UUID
    ) -> PresignedUrl:
        async with self._uow() as uow:
            statement = (await self._own(uow, claims, application_id)).statement(statement_id)
        files = self._statements
        ttl_sec = int(files.url_ttl.total_seconds())
        url = await files.storage.presigned_get(
            files.bucket, statement.file_key, ttl_sec, statement.file_name
        )
        return PresignedUrl(
            url=url, file_name=statement.file_name, expires_at=self._clock.now() + files.url_ttl
        )

    async def _delete_file(self, key: str) -> None:
        try:
            await self._statements.storage.delete(self._statements.bucket, key)
        except StorageUnavailable:
            # Строки в БД уже нет, клиенту ошибка ничего не даст: объект останется сиротой.
            logger.warning("Не удалось удалить из S3 файл %s", key)

    async def submit(self, claims: TokenClaims, application_id: UUID) -> Application:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            checks = await self._run_validator(uow, app)
            app.submit(
                self._product(app.product_code), actor_id=claims.user_id, now=self._clock.now()
            )
            app.record_checks(CheckStage.FORM, checks)
            await uow.commit()
        await self._publish(app)
        await start_pipeline(self._queue, app)
        logger.info("Заявка %s отправлена в конвейер", app.id)
        return app

    async def accept(
        self, claims: TokenClaims, application_id: UUID, offer_id: UUID | None
    ) -> Application:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            app.accept_offer(offer_id, actor_id=claims.user_id, now=self._clock.now())
            await uow.commit()
        await self._publish(app)
        return app

    async def cancel(
        self, claims: TokenClaims, application_id: UUID, reason: str | None
    ) -> Application:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            app.cancel(
                actor_id=claims.user_id,
                reason=reason or CLIENT_CANCEL_REASON,
                now=self._clock.now(),
            )
            await uow.commit()
        await self._publish(app)
        return app

    async def get_for_client(
        self, claims: TokenClaims, application_id: UUID
    ) -> ClientApplicationView:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
        return self.view(app, claims)

    @staticmethod
    def view(app: Application, claims: TokenClaims) -> ClientApplicationView:
        return ClientApplicationView(
            application=app,
            allowed_actions=ApplicationStateMachine.allowed_actions(app, claims.role),
        )

    async def list_for_client(
        self, claims: TokenClaims, status: ApplicationStatus | None, page: int, size: int
    ) -> ApplicationPage:
        async with self._uow() as uow:
            items, total = await uow.applications.list_for_applicant(
                claims.user_id, status=status, offset=(page - 1) * size, limit=size
            )
        return ApplicationPage(items=items, total=total, page=page, size=size)

    async def history(self, claims: TokenClaims, application_id: UUID) -> list[HistoryEntry]:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            return await load_history(uow, app)

    async def decision(self, claims: TokenClaims, application_id: UUID) -> DecisionView:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
        decision = app.current_decision
        if decision is None:
            raise NotFound("Решения по заявке ещё нет")
        return DecisionView(decision=decision, scoring=app.scoring, expires_at=app.expires_at)

    async def findings(self, claims: TokenClaims, application_id: UUID) -> ApplicationFindings:
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
        if app.status in COLLECTING_STATUSES or not app.snapshots:
            raise InvalidStatus("Проверка по источникам ещё идёт или не запускалась")
        return self._findings.build(app)

    async def _run_validator(self, uow: UnitOfWork, app: Application) -> list[CheckResult]:
        app.replace_consents(await uow.consents.list_for(app.inn, app.applicant_id))
        applicant = await uow.users.get(app.applicant_id)
        return await self._validator.validate(app, applicant, uow.applications)

    async def _own(self, uow: UnitOfWork, claims: TokenClaims, application_id: UUID) -> Application:
        app = await uow.applications.get(application_id)
        if claims.role is Role.CLIENT and app.applicant_id != claims.user_id:
            raise Forbidden("Это чужая заявка")
        return app

    def _product(self, code: str) -> LoanProduct:
        try:
            return self._catalog.get(code)
        except NotFound:
            raise ValidationFailed(
                [FieldError("product_code", "FORM_COMPLETE", "Такого продукта нет")]
            ) from None

    async def _publish(self, app: Application) -> None:
        for event in app.pull_events():
            await self._publisher.publish(event)


async def load_history(uow: UnitOfWork, app: Application) -> list[HistoryEntry]:
    """Переходы заявки с ролью и именем автора; удалённый пользователь показывается системой."""
    actors: dict[UUID, tuple[Role, str]] = {}
    for actor_id in {t.actor_id for t in app.history if t.actor_id is not None}:
        try:
            user = await uow.users.get(actor_id)
        except NotFound:
            continue
        actors[actor_id] = (user.role, user.full_name)
    entries = []
    for t in app.history:
        role, name = (
            actors.get(t.actor_id, (None, SYSTEM_ACTOR_NAME))
            if t.actor_id is not None
            else (None, SYSTEM_ACTOR_NAME)
        )
        entries.append(HistoryEntry(transition=t, actor_role=role, actor_name=name))
    return entries


def _safe_file_name(name: str) -> str:
    """Имя без пути (браузеры присылают C:\\fakepath\\...), не длиннее колонки, с расширением."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip() or DEFAULT_STATEMENT_NAME
    if len(base) <= MAX_NAME_LENGTH:
        return base
    stem, dot, ext = base.rpartition(".")
    if not dot or len(ext) >= MAX_NAME_LENGTH // 2:
        return base[:MAX_NAME_LENGTH]
    return stem[: MAX_NAME_LENGTH - len(ext) - 1] + "." + ext
