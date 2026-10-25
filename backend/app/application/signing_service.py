"""Подписание кодом и выдача: договор, PDF версии 1 в S3, ISSUED (BACKEND_SPEC, раздел 17)."""

import logging
import secrets
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from app.application.contract_service import ContractDocuments
from app.domain.entities import Application, Contract, SigningCode, TokenClaims
from app.domain.entities.contract import contract_number
from app.domain.enums import Role
from app.domain.errors import Forbidden
from app.domain.ports import Clock, StatusPublisher, UnitOfWork
from app.domain.products import ContractOpening, ProductCatalog

logger = logging.getLogger(__name__)

CODE_DIGITS = 6


class SigningService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        catalog: ProductCatalog,
        documents: ContractDocuments,
        publisher: StatusPublisher,
        clock: Clock,
        code_ttl: timedelta,
    ) -> None:
        self._uow = uow
        self._catalog = catalog
        self._documents = documents
        self._publisher = publisher
        self._clock = clock
        self._code_ttl = code_ttl

    async def issue_code(self, claims: TokenClaims, application_id: UUID) -> SigningCode:
        code = SigningCode(
            code=f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}",
            expires_at=self._clock.now() + self._code_ttl,
        )
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            app.issue_signing_code(code)
            await uow.commit()
        return code

    async def confirm(self, claims: TokenClaims, application_id: UUID, code: str) -> Contract:
        now = self._clock.now()
        today = self._clock.today()
        async with self._uow() as uow:
            app = await self._own(uow, claims, application_id)
            app.verify_signing_code(code, now=now)
            product = self._catalog.get(app.product_code)
            contract_id = uuid4()
            contract = product.open_contract(
                app,
                ContractOpening(
                    contract_id=contract_id,
                    number=contract_number(today.year, await uow.contracts.next_sequence()),
                    rate=app.agreed_rate(),
                    document_key=self._documents.key(contract_id, 1),
                    signer_id=claims.user_id,
                    now=now,
                    today=today,
                ),
            )
            app.issue(contract.id, actor_id=claims.user_id, now=now)
            await uow.contracts.add(contract)
            await self._documents.commit_with_document(
                uow, self._documents.data(contract, app, product)
            )
        for event in app.pull_events():
            await self._publisher.publish(event)
        logger.info("Заявка %s выдана, договор %s", app.id, contract.number)
        return contract

    async def _own(self, uow: UnitOfWork, claims: TokenClaims, application_id: UUID) -> Application:
        app = await uow.applications.get(application_id)
        if claims.role is not Role.CLIENT or app.applicant_id != claims.user_id:
            raise Forbidden("Подписать может только сам заявитель")
        return app
