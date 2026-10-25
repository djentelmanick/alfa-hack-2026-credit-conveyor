"""Генератор выписок 1С для сценария 4 (ИП Иванова, кофейня).

Выписки детерминированные: выручка ровно 15 млн ₽ в пересчёте на год и ровная по кварталам.
Даты зашиты в файлы, а свежесть выписки проверяется от текущей даты, поэтому перед демо
в другой день файлы перегенерируют:

    python -m app.infra.mocks.statement_samples --end 2026-11-20 --out /tmp/statements
"""

import argparse
import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.domain.dates import add_months
from app.domain.value_objects import Money, Period

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "data" / "statements"
DEFAULT_END = date(2026, 10, 23)
ANNUAL_REVENUE = Money.of(15_000_000)

_EOL = "\r\n"


@dataclass(frozen=True, slots=True)
class Party:
    name: str
    inn: str
    account: str


OWNER = Party("ИП ИВАНОВА ЕЛЕНА СЕРГЕЕВНА", "770512345650", "40802810400000012345")
BANK_NAME = 'АО "СЕВЕРНЫЙ БАНК"'
ACQUIRER = Party(BANK_NAME, "7702987655", "30233810100000000777")
SUPPLIER = Party('ООО "КОФЕ ТРЕЙД"', "7720123457", "40702810900000054321")
LANDLORD = Party("ИП БЕЛОВ СЕРГЕЙ ПЕТРОВИЧ", "771067890151", "40802810700000098765")

# Кофейня продаёт больше в выходные: веса по дням недели с понедельника.
_WEEKDAY_WEIGHTS = (
    Decimal("1.00"),
    Decimal("1.00"),
    Decimal("1.00"),
    Decimal("1.05"),
    Decimal("1.15"),
    Decimal("1.30"),
    Decimal("1.20"),
)
_SUPPLIER_PAYMENT = Money.of(95_000)
_RENT = Money.of(150_000)
_RENT_DAY = 5


@dataclass(frozen=True, slots=True)
class _Operation:
    day: date
    amount: Money
    payer: Party
    receiver: Party
    purpose: str
    kind: str


def build_statement(start: date, end: date, *, signed: bool) -> bytes:
    months = Period(start, end).months()
    operations = _incoming(start, end, ANNUAL_REVENUE * months / 12) + _outgoing(start, end)
    operations.sort(key=lambda op: (op.day, op.payer is not OWNER))
    body = _render(start, end, operations).encode("cp1251")
    if not signed:
        return body
    digest = hashlib.sha256(body).hexdigest()
    block = _EOL.join(
        [
            "СекцияПодписьБанка",
            f"Подписант={BANK_NAME}",
            "СертификатДо=31.12.2027",
            f"ХешДокумента=sha256:{digest}",
            "КонецПодписиБанка",
            "",
        ]
    )
    return body + block.encode("cp1251")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _incoming(start: date, end: date, total: Money) -> list[_Operation]:
    days = _days(start, end)
    weights = [_WEEKDAY_WEIGHTS[d.weekday()] for d in days]
    weight_sum = sum(weights, Decimal(0))
    amounts = [Money(total.amount * w / weight_sum) for w in weights]
    # Копейки от округления добирает последний день, чтобы итог был ровно total.
    amounts[-1] = total - sum(amounts[:-1], Money.zero())
    return [
        _Operation(
            day=day,
            amount=amount,
            payer=ACQUIRER,
            receiver=OWNER,
            purpose=f"Возмещение по эквайрингу за {day - timedelta(days=1):%d.%m.%Y}. Без НДС",
            kind="Банковский ордер",
        )
        for day, amount in zip(days, amounts, strict=True)
    ]


def _outgoing(start: date, end: date) -> list[_Operation]:
    operations: list[_Operation] = []
    for day in _days(start, end):
        if day.weekday() == 0:
            operations.append(
                _Operation(
                    day=day,
                    amount=_SUPPLIER_PAYMENT,
                    payer=OWNER,
                    receiver=SUPPLIER,
                    purpose="Оплата за кофе в зёрнах и молочную продукцию по договору №17. Без НДС",
                    kind="Платежное поручение",
                )
            )
        if day.day == _RENT_DAY:
            operations.append(
                _Operation(
                    day=day,
                    amount=_RENT,
                    payer=OWNER,
                    receiver=LANDLORD,
                    purpose=f"Арендная плата за {day:%m.%Y} по договору аренды №3. Без НДС",
                    kind="Платежное поручение",
                )
            )
    return operations


def _render(start: date, end: date, operations: list[_Operation]) -> str:
    incoming = sum((op.amount for op in operations if op.receiver is OWNER), Money.zero())
    outgoing = sum((op.amount for op in operations if op.payer is OWNER), Money.zero())
    opening = Money.of(250_000)
    lines = [
        "1CClientBankExchange",
        "ВерсияФормата=1.03",
        "Кодировка=Windows",
        f"Отправитель={BANK_NAME}",
        "Получатель=",
        f"ДатаСоздания={end + timedelta(days=1):%d.%m.%Y}",
        "ВремяСоздания=09:00:00",
        f"ДатаНачала={start:%d.%m.%Y}",
        f"ДатаКонца={end:%d.%m.%Y}",
        f"РасчСчет={OWNER.account}",
        "СекцияРасчСчет",
        f"ДатаНачала={start:%d.%m.%Y}",
        f"ДатаКонца={end:%d.%m.%Y}",
        f"РасчСчет={OWNER.account}",
        f"НачальныйОстаток={opening}",
        f"ВсегоПоступило={incoming}",
        f"ВсегоСписано={outgoing}",
        f"КонечныйОстаток={opening + incoming - outgoing}",
        "КонецРасчСчет",
    ]
    for number, op in enumerate(operations, start=1):
        lines += [
            f"СекцияДокумент={op.kind}",
            f"Номер={number}",
            f"Дата={op.day:%d.%m.%Y}",
            f"Сумма={op.amount}",
            f"ПлательщикСчет={op.payer.account}",
            f"Плательщик={op.payer.name}",
            f"ПлательщикИНН={op.payer.inn}",
            f"ПолучательСчет={op.receiver.account}",
            f"Получатель={op.receiver.name}",
            f"ПолучательИНН={op.receiver.inn}",
            f"НазначениеПлатежа={op.purpose}",
            "КонецДокумента",
        ]
    lines += ["КонецФайла", ""]
    return _EOL.join(lines)


def write_samples(out_dir: Path, end: date) -> list[Path]:
    """Две выписки сценария 4: за 4 месяца без подписи (дозапрос) и за 12 месяцев с ЭП банка."""
    out_dir.mkdir(parents=True, exist_ok=True)
    next_day = end + timedelta(days=1)
    samples = {
        "ivanova_4m.txt": build_statement(add_months(next_day, -4), end, signed=False),
        "ivanova_12m_signed.txt": build_statement(add_months(next_day, -12), end, signed=True),
    }
    paths = []
    for name, content in samples.items():
        path = out_dir / name
        path.write_bytes(content)
        paths.append(path)
    return paths


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    for path in write_samples(args.out, args.end):
        logger.info("Записана выписка %s", path)


if __name__ == "__main__":
    main()
