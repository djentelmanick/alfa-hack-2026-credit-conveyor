import asyncio

from app.domain.enums import SourceType
from app.domain.errors import SourceUnavailable
from app.infra.mocks.synthetic import SyntheticData
from app.infra.retrying import Sleep


class MockFailures:
    """Режим «сломаться» для демо, общий для всех моков и управляемый через /dev/mocks/failures.

    Ключ это источник, а не адаптер: один MockFnsAdapter отвечает и за ФНС, и за ГИР БО,
    и их нужно уметь ломать по отдельности. Этот класс держит поломки в памяти процесса
    (тесты, где api и worker в одном процессе); приложение использует SqlMockFailures.
    """

    def __init__(self) -> None:
        self._broken: dict[SourceType, set[str]] = {}

    async def set(self, source: SourceType, inn: str, *, enabled: bool) -> None:
        inns = self._broken.setdefault(source, set())
        if enabled:
            inns.add(inn)
        else:
            inns.discard(inn)

    async def failing_inns(self, source: SourceType) -> frozenset[str]:
        return frozenset(self._broken.get(source, ()))

    async def clear(self) -> None:
        self._broken.clear()


class MockSourceAdapter:
    def __init__(
        self,
        fixtures: SyntheticData,
        failures: MockFailures,
        *,
        delay_ms: int = 0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._fixtures = fixtures
        self._failures = failures
        self._delay_ms = delay_ms
        self._sleep = sleep

    async def _respond(self, source: SourceType | None, *inns: str) -> None:
        """Пауза PIPELINE_STEP_DELAY_MS (на демо видна смена статусов), затем проверка поломки."""
        if self._delay_ms:
            await self._sleep(self._delay_ms / 1000)
        if source is not None:
            await self.raise_if_broken(source, *inns)

    async def raise_if_broken(self, source: SourceType, *inns: str) -> None:
        # Ломают по ИНН компании, а БКИ и ФССП спрашивают ещё и про директора с учредителями.
        # Поэтому физлицо считается сломанным, если сломана любая его компания.
        failing = await self._failures.failing_inns(source)
        if not failing:
            return
        for inn in inns:
            if inn in failing or self._fixtures.companies_of(inn) & failing:
                raise SourceUnavailable(f"Источник {source} не отвечает (режим «сломаться»)")
