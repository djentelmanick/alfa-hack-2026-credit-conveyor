"""Повтор запросов к источникам (SCOPE, раздел 4: «источник не ответил»).

Обёртки реализуют те же порты, что и адаптеры, поэтому конвейер не знает о повторах,
а настоящую интеграцию можно подставить под ту же обёртку в main.py.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.domain.authority import PowerOfAttorney
from app.domain.entities import Consent
from app.domain.enums import KycLevel, SignatureStatus
from app.domain.errors import SourceUnavailable
from app.domain.ports import (
    AccountTurnoverPort,
    BailiffPort,
    CbrKycPort,
    CompanyRegistryPort,
    CreditBureauPort,
    MchdRegistryPort,
    NotaryPoaRegistryPort,
    PassportCheckPort,
    RosfinmonitoringPort,
    SignatureVerifierPort,
    TaxServicePort,
)
from app.domain.value_objects import (
    BailiffInfo,
    CompanyProfile,
    CreditReport,
    Money,
    MonthlyTurnover,
    TaxInfo,
)

logger = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """attempts это общее число попыток, включая первую (SOURCE_RETRY_ATTEMPTS)."""

    attempts: int
    pause_ms: int

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("Нужна хотя бы одна попытка")
        if self.pause_ms < 0:
            raise ValueError("Пауза не может быть отрицательной")


class RetryingGateway[P]:
    def __init__(self, inner: P, policy: RetryPolicy, *, sleep: Sleep = asyncio.sleep) -> None:
        self._inner = inner
        self._policy = policy
        self._sleep = sleep

    @property
    def inner(self) -> P:
        return self._inner

    async def _call[T](self, operation: str, call: Callable[[P], Awaitable[T]]) -> T:
        # Повторяем только недоступность. NotFound и ошибки данных от повтора не исправятся.
        attempts = self._policy.attempts
        for attempt in range(1, attempts + 1):
            try:
                return await call(self._inner)
            except SourceUnavailable as exc:
                logger.warning(
                    "Источник не ответил: %s, попытка %d из %d", operation, attempt, attempts
                )
                if attempt == attempts:
                    raise SourceUnavailable(
                        f"{exc.message}: {operation}, попыток {attempts}"
                    ) from exc
                await self._sleep(self._policy.pause_ms / 1000)
        raise AssertionError("unreachable")


class RetryingCompanyRegistry(RetryingGateway[CompanyRegistryPort], CompanyRegistryPort):
    async def find_by_inn(self, inn: str) -> CompanyProfile:
        return await self._call("ЕГРЮЛ.find_by_inn", lambda p: p.find_by_inn(inn))


class RetryingCreditBureau(RetryingGateway[CreditBureauPort], CreditBureauPort):
    async def report(self, subject_inn: str, consent: Consent) -> CreditReport:
        return await self._call("БКИ.report", lambda p: p.report(subject_inn, consent))


class RetryingAccountTurnover(RetryingGateway[AccountTurnoverPort], AccountTurnoverPort):
    async def monthly_turnover(self, inn: str, months: int) -> MonthlyTurnover | None:
        return await self._call(
            "обороты.monthly_turnover", lambda p: p.monthly_turnover(inn, months)
        )


class RetryingTaxService(RetryingGateway[TaxServicePort], TaxServicePort):
    async def account_blocks(self, inn: str) -> bool:
        return await self._call("ФНС.account_blocks", lambda p: p.account_blocks(inn))

    async def is_mass_address(self, address: str) -> bool:
        return await self._call("ФНС.is_mass_address", lambda p: p.is_mass_address(address))

    async def annual_revenue(self, inn: str, year: int) -> Money | None:
        return await self._call("ГИР БО.annual_revenue", lambda p: p.annual_revenue(inn, year))

    async def tax_info(self, inn: str) -> TaxInfo:
        return await self._call("ФНС.tax_info", lambda p: p.tax_info(inn))


class RetryingBailiff(RetryingGateway[BailiffPort], BailiffPort):
    async def open_proceedings(self, inn: str) -> BailiffInfo:
        return await self._call("ФССП.open_proceedings", lambda p: p.open_proceedings(inn))


class RetryingCbrKyc(RetryingGateway[CbrKycPort], CbrKycPort):
    async def risk_level(self, inn: str) -> KycLevel:
        return await self._call("ЦБ.risk_level", lambda p: p.risk_level(inn))


class RetryingRosfinmonitoring(RetryingGateway[RosfinmonitoringPort], RosfinmonitoringPort):
    async def is_listed(self, inn: str, persons: Sequence[str]) -> bool:
        return await self._call("Росфинмониторинг.is_listed", lambda p: p.is_listed(inn, persons))


class RetryingSignatureVerifier(RetryingGateway[SignatureVerifierPort], SignatureVerifierPort):
    async def verify(self, statement_raw: bytes) -> SignatureStatus:
        return await self._call("ЭП.verify", lambda p: p.verify(statement_raw))


class RetryingPassportCheck(RetryingGateway[PassportCheckPort], PassportCheckPort):
    async def is_valid(self, series: str, number: str) -> bool:
        return await self._call("паспорт.is_valid", lambda p: p.is_valid(series, number))


class RetryingMchdRegistry(RetryingGateway[MchdRegistryPort], MchdRegistryPort):
    async def find(
        self, guid: UUID, principal_inn: str, representative_inn: str
    ) -> PowerOfAttorney | None:
        return await self._call(
            "МЧД.find", lambda p: p.find(guid, principal_inn, representative_inn)
        )


class RetryingNotaryPoaRegistry(RetryingGateway[NotaryPoaRegistryPort], NotaryPoaRegistryPort):
    async def find(
        self, notarized_on: date, register_number: str, notary_name: str
    ) -> PowerOfAttorney | None:
        return await self._call(
            "ФНП.find", lambda p: p.find(notarized_on, register_number, notary_name)
        )
