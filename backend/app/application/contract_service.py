"""Договоры после выдачи: просмотр, график, версии, журнал и PDF (SCOPE, раздел 8)."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from app.application.application_service import SYSTEM_ACTOR_NAME, PresignedUrl
from app.domain.dates import add_months
from app.domain.entities import (
    Application,
    Contract,
    ContractVersion,
    CreditLineContract,
    LoanContract,
    Operation,
    ScheduleItem,
    TokenClaims,
    Tranche,
)
from app.domain.enums import ContractAction, RepaymentKind, Role
from app.domain.errors import (
    FieldError,
    Forbidden,
    InvalidStatus,
    NotFound,
    StorageUnavailable,
    ValidationFailed,
)
from app.domain.ports import (
    Clock,
    ContractDocumentData,
    ContractDocumentRenderer,
    FileStoragePort,
    UnitOfWork,
)
from app.domain.products import LoanProduct, ProductCatalog, RevolvingCreditLine
from app.domain.value_objects import Money

logger = logging.getLogger(__name__)

PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractDocuments:
    """PDF версий договора в S3. Общий для подписания и правки договора аналитиком."""

    renderer: ContractDocumentRenderer
    storage: FileStoragePort
    bucket: str
    url_ttl: timedelta

    @staticmethod
    def key(contract_id: UUID, version_no: int) -> str:
        return f"{contract_id}/v{version_no}.pdf"

    @staticmethod
    def file_name(contract: Contract, version_no: int) -> str:
        return f"{contract.number}_v{version_no}.pdf"

    @staticmethod
    def data(contract: Contract, app: Application, product: LoanProduct) -> ContractDocumentData:
        return ContractDocumentData(
            contract=contract,
            version=contract.current_version,
            product_name=product.name,
            company_name=app.company_name,
            inn=app.inn,
            collateral=app.collateral,
        )

    async def commit_with_document(self, uow: UnitOfWork, data: ContractDocumentData) -> None:
        """Сначала PDF в S3, потом commit: договор без документа появиться не должен.

        Если S3 не ответил, исключение уходит до commit, и единица работы всё откатывает.
        """
        key = data.version.document_key
        pdf = await self.renderer.render(data)
        await self.storage.put(self.bucket, key, pdf, PDF_CONTENT_TYPE)
        try:
            await uow.commit()
        except Exception:
            await self.discard(key)
            raise

    async def discard(self, key: str) -> None:
        try:
            await self.storage.delete(self.bucket, key)
        except StorageUnavailable:
            # Версия в БД не сохранилась, ссылку на файл никто не получит: он останется сиротой.
            logger.warning("Не удалось удалить из S3 PDF договора %s", key)


@dataclass(frozen=True, kw_only=True, slots=True)
class ContractView:
    contract: Contract
    next_payment: ScheduleItem | None
    draw_until: date | None
    allowed_operations: tuple[ContractAction, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class VersionView:
    version: ContractVersion
    author_role: Role | None
    author_name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationPage:
    items: list[Operation]
    total: int
    page: int
    size: int


class ContractService:
    """Клиент видит только договоры по своим заявкам, аналитик видит все."""

    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        catalog: ProductCatalog,
        documents: ContractDocuments,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._catalog = catalog
        self._documents = documents
        self._clock = clock

    async def get(self, claims: TokenClaims, contract_id: UUID) -> ContractView:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
        return self.view(contract)

    async def list_for(
        self, claims: TokenClaims, application_id: UUID | None = None
    ) -> list[Contract]:
        applicant_id = claims.user_id if claims.role is Role.CLIENT else None
        async with self._uow() as uow:
            return await uow.contracts.list_for(
                applicant_id=applicant_id, application_id=application_id
            )

    async def schedule(
        self, claims: TokenClaims, contract_id: UUID, version_no: int | None = None
    ) -> tuple[ScheduleItem, ...]:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
        return _version(contract, version_no).schedule

    async def operations(
        self, claims: TokenClaims, contract_id: UUID, page: int, size: int
    ) -> OperationPage:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
        offset = (page - 1) * size
        items = contract.operations[offset : offset + size]
        return OperationPage(items=items, total=len(contract.operations), page=page, size=size)

    async def versions(self, claims: TokenClaims, contract_id: UUID) -> list[VersionView]:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
            authors = await _authors(uow, (v.author_id for v in contract.versions))
        views = []
        for v in contract.versions:
            author = authors.get(v.author_id) if v.author_id is not None else None
            role, name = author if author is not None else (None, SYSTEM_ACTOR_NAME)
            views.append(VersionView(version=v, author_role=role, author_name=name))
        return views

    async def document_url(
        self, claims: TokenClaims, contract_id: UUID, version_no: int | None = None
    ) -> PresignedUrl:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
        version = _version(contract, version_no)
        if not version.has_document:
            raise NotFound("У этой версии договора нет документа")
        documents = self._documents
        file_name = documents.file_name(contract, version.version_no)
        url = await documents.storage.presigned_get(
            documents.bucket,
            version.document_key,
            int(documents.url_ttl.total_seconds()),
            file_name,
        )
        return PresignedUrl(
            url=url,
            file_name=file_name,
            expires_at=self._clock.now() + documents.url_ttl,
            version_no=version.version_no,
        )

    async def draw_tranche(self, claims: TokenClaims, contract_id: UUID, amount: Money) -> Tranche:
        async with self._uow() as uow:
            contract = await self._own_for_operation(uow, claims, contract_id)
            if not isinstance(contract, CreditLineContract):
                raise InvalidStatus("Транши есть только у кредитной линии")
            contract.ensure_active()
            product = self._line_product(contract)
            errors = product.draw_errors(contract, amount, contract.contract_date)
            if errors:
                raise ValidationFailed(errors)
            tranche = contract.draw(
                amount,
                due_at=add_months(contract.contract_date, product.tranche_term_months),
                actor_id=claims.user_id,
            )
            await uow.commit()
        logger.info("Договор %s: транш %s на %s", contract.id, tranche.id, amount)
        return tranche

    async def repay_tranche(
        self, claims: TokenClaims, contract_id: UUID, tranche_id: UUID, amount: Money
    ) -> ContractView:
        async with self._uow() as uow:
            contract = await self._own_for_operation(uow, claims, contract_id)
            if not isinstance(contract, CreditLineContract):
                raise InvalidStatus("Транши есть только у кредитной линии")
            contract.repay_tranche(tranche_id, amount, actor_id=claims.user_id)
            await uow.commit()
        logger.info("Договор %s: погашение транша %s на %s", contract.id, tranche_id, amount)
        return self.view(contract)

    async def repay(
        self, claims: TokenClaims, contract_id: UUID, kind: RepaymentKind, amount: Money | None
    ) -> ContractView:
        """FULL закрывает договор любого продукта, PARTIAL есть только у кредита одной суммой."""
        async with self._uow() as uow:
            contract = await self._own_for_operation(uow, claims, contract_id)
            if kind is RepaymentKind.FULL:
                contract.repay_full(claims.user_id)
                await uow.commit()
            else:
                await self._prepay(uow, claims, contract, amount)
        logger.info("Договор %s: досрочное погашение %s", contract.id, kind)
        return self.view(contract)

    async def _prepay(
        self, uow: UnitOfWork, claims: TokenClaims, contract: Contract, amount: Money | None
    ) -> None:
        if not isinstance(contract, LoanContract):
            raise InvalidStatus("У кредитной линии нет частичного досрочного погашения")
        if amount is None:
            raise ValidationFailed(
                [FieldError("amount", "REQUIRED", "Укажите сумму частичного погашения")]
            )
        product = self._catalog.get(contract.product_code)
        documents = self._documents
        contract.prepay(
            amount,
            rebuild=lambda c: product.reschedule(c, c.rate, c.term_months),
            document_key=documents.key(contract.id, contract.next_version_no),
            author_id=claims.user_id,
            now=self._clock.now(),
        )
        app = await uow.applications.get(contract.application_id)
        await documents.commit_with_document(uow, documents.data(contract, app, product))

    def view(self, contract: Contract) -> ContractView:
        product = self._catalog.get(contract.product_code)
        match contract:
            case LoanContract():
                return ContractView(
                    contract=contract,
                    next_payment=contract.next_payment(),
                    draw_until=None,
                    allowed_operations=(
                        (ContractAction.PARTIAL_PREPAYMENT, ContractAction.FULL_REPAYMENT)
                        if contract.is_active()
                        else ()
                    ),
                )
            case CreditLineContract() if isinstance(product, RevolvingCreditLine):
                draw_until = product.draw_until(contract)
                return ContractView(
                    contract=contract,
                    next_payment=None,
                    draw_until=draw_until,
                    allowed_operations=_line_actions(contract, product, draw_until),
                )
        raise TypeError(f"Договор {contract.number} не подходит к продукту {product.code}")

    async def _own_for_operation(
        self, uow: UnitOfWork, claims: TokenClaims, contract_id: UUID
    ) -> Contract:
        """Операции с деньгами проводит только сам клиент по своему договору."""
        if claims.role is not Role.CLIENT:
            raise Forbidden("Операции по договору проводит клиент")
        return await load_visible(uow, claims, contract_id)

    def _line_product(self, contract: CreditLineContract) -> RevolvingCreditLine:
        product = self._catalog.get(contract.product_code)
        if not isinstance(product, RevolvingCreditLine):
            raise TypeError(f"Договор {contract.number} не подходит к продукту {product.code}")
        return product


async def load_visible(uow: UnitOfWork, claims: TokenClaims, contract_id: UUID) -> Contract:
    """Клиент видит только договоры по своим заявкам, аналитик видит все."""
    contract = await uow.contracts.get(contract_id)
    if claims.role is Role.CLIENT:
        app = await uow.applications.get(contract.application_id)
        if app.applicant_id != claims.user_id:
            raise Forbidden("Это чужой договор")
    return contract


def _version(contract: Contract, version_no: int | None) -> ContractVersion:
    version = contract.version(version_no)
    if version is None:
        raise NotFound(f"Версии {version_no} у договора нет")
    return version


def _line_actions(
    contract: CreditLineContract, product: RevolvingCreditLine, draw_until: date
) -> tuple[ContractAction, ...]:
    if not contract.is_active():
        return ()
    actions = []
    if contract.contract_date < draw_until and contract.available >= product.tranche_min:
        actions.append(ContractAction.DRAW_TRANCHE)
    if any(t.outstanding.is_positive() for t in contract.tranches):
        actions.append(ContractAction.REPAY_TRANCHE)
    actions.append(ContractAction.FULL_REPAYMENT)
    return tuple(actions)


async def _authors(uow: UnitOfWork, ids: Iterable[UUID | None]) -> dict[UUID, tuple[Role, str]]:
    """Роль и имя авторов; удалённый пользователь показывается системой."""
    found: dict[UUID, tuple[Role, str]] = {}
    for user_id in {i for i in ids if i is not None}:
        try:
            user = await uow.users.get(user_id)
        except NotFound:
            continue
        found[user_id] = (user.role, user.full_name)
    return found
