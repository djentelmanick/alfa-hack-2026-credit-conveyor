from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.entities import Consent
from app.domain.enums import CompanyKind, ConsentType
from app.infra.mocks import MockFailures, SyntheticData

# Дата, на которую подобрана синтетика (SCOPE, раздел 12).
TODAY = date(2026, 10, 24)

STATEMENTS_DIR = (
    Path(__file__).resolve().parents[3] / "app" / "infra" / "mocks" / "data" / "statements"
)

KOLOS = "7714012340"
ROMASHKA = "7703123457"
SOKOLOV = "770498765403"
IVANOVA = "770512345650"
TECHPROM = "7704123450"
ALL_INNS = (KOLOS, ROMASHKA, SOKOLOV, IVANOVA, TECHPROM)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Исходные данные сценария из SCOPE, раздел 12. Их нельзя подгонять под синтетику."""

    inn: str
    kind: CompanyKind
    amount: Decimal
    term_months: int
    min_rate: Decimal
    age_years: int | None
    annual_revenue: Decimal | None
    monthly_payments: Decimal
    has_history: bool
    has_closed_loans: bool
    okved: str
    approx_load_pct: Decimal | None


SCENARIOS = (
    Scenario(
        KOLOS,
        CompanyKind.LLC,
        Decimal(2_000_000),
        12,
        Decimal("23.5"),
        5,
        Decimal(24_000_000),
        Decimal(50_000),
        True,
        True,
        "56.10",
        Decimal(12),
    ),
    Scenario(
        SOKOLOV,
        CompanyKind.IE,
        Decimal(4_500_000),
        24,
        Decimal("23.5"),
        4,
        Decimal(12_000_000),
        Decimal(100_000),
        True,
        False,
        "47.52",
        Decimal(34),
    ),
    Scenario(
        IVANOVA,
        CompanyKind.IE,
        Decimal(3_000_000),
        24,
        Decimal("23.5"),
        None,
        None,
        Decimal(0),
        False,
        False,
        "56.10",
        Decimal(13),
    ),
    Scenario(
        TECHPROM,
        CompanyKind.LLC,
        Decimal(12_000_000),
        60,
        Decimal("23.0"),
        7,
        Decimal(60_000_000),
        Decimal(400_000),
        True,
        True,
        "25.62",
        Decimal(15),
    ),
)


class RecordingSleep:
    """Подмена asyncio.sleep: тесты не ждут, а проверяют, какие паузы были бы."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture(scope="session")
def fixtures() -> SyntheticData:
    return SyntheticData.load()


@pytest.fixture
def failures() -> MockFailures:
    return MockFailures()


@pytest.fixture
def consent() -> Consent:
    return Consent(
        type=ConsentType.BKI_COMPANY, given_by=uuid4(), given_at=datetime(2026, 10, 24, tzinfo=UTC)
    )


@pytest.fixture
def statement_4m() -> bytes:
    return (STATEMENTS_DIR / "ivanova_4m.txt").read_bytes()


@pytest.fixture
def statement_12m() -> bytes:
    return (STATEMENTS_DIR / "ivanova_12m_signed.txt").read_bytes()
