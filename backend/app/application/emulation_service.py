"""Эмуляция времени договора для демо (SCOPE, раздел 8; BACKEND_SPEC, раздел 15).

Двигается только contract_date этого договора: порт Clock и сроки заявок эмуляция не трогает.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.application.contract_service import (
    ContractDocuments,
    ContractService,
    ContractView,
    load_visible,
)
from app.domain.entities import Operation, TokenClaims
from app.domain.errors import FieldError, ValidationFailed
from app.domain.ports import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class EmulationResult:
    contract_date: date
    operations: list[Operation]
    contract: ContractView


class EmulationService:
    def __init__(
        self,
        *,
        uow: Callable[[], UnitOfWork],
        contracts: ContractService,
        documents: ContractDocuments,
    ) -> None:
        self._uow = uow
        self._contracts = contracts
        self._documents = documents

    async def next_month(self, claims: TokenClaims, contract_id: UUID) -> EmulationResult:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
            operations = contract.advance_month()
            await uow.commit()
        logger.info("Договор %s: эмуляция до %s", contract.id, contract.contract_date)
        return EmulationResult(
            contract_date=contract.contract_date,
            operations=operations,
            contract=self._contracts.view(contract),
        )

    async def rewind_to(
        self, claims: TokenClaims, contract_id: UUID, target: date
    ) -> EmulationResult:
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
            contract.ensure_active()
            if target < contract.contract_date:
                raise ValidationFailed(
                    [FieldError("target_date", "DATE_IN_PAST", "Дата раньше текущей даты договора")]
                )
            # Без верхней границы перемотка ВКЛ с автопродлением не закончилась бы никогда.
            if target > contract.end_date:
                raise ValidationFailed(
                    [FieldError("target_date", "DATE_AFTER_END", "Дата позже окончания договора")]
                )
            operations = contract.move_date(target)
            await uow.commit()
        logger.info("Договор %s: перемотка до %s", contract.id, contract.contract_date)
        return EmulationResult(
            contract_date=contract.contract_date,
            operations=operations,
            contract=self._contracts.view(contract),
        )

    async def reset(self, claims: TokenClaims, contract_id: UUID) -> ContractView:
        """PDF удалённых версий чистится после commit: если S3 не ответит, файл останется."""
        async with self._uow() as uow:
            contract = await load_visible(uow, claims, contract_id)
            removed = contract.reset_to_issued()
            await uow.commit()
        for key in removed:
            await self._documents.discard(key)
        logger.info("Договор %s: сброс к состоянию после выдачи", contract.id)
        return self._contracts.view(contract)
