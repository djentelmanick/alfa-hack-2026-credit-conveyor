"""Разбор выписки в формате 1CClientBankExchange (docs/data-sources/04-1c-statement.md)."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Never, Self

from app.domain.entities import ParsedStatement, StatementOperation
from app.domain.enums import Direction
from app.domain.errors import FieldError, ValidationFailed
from app.domain.ports import StatementParser
from app.domain.value_objects import Money, Period

FORMAT_MARKER = "1CClientBankExchange"
SIGNATURE_START = "СекцияПодписьБанка"
SIGNATURE_END = "КонецПодписиБанка"
_UTF8_BOM = b"\xef\xbb\xbf"


def decode_1c(raw: bytes) -> str:
    """Стандартная кодировка формата Windows-1251, но часть банков выгружает UTF-8."""
    if raw.startswith(_UTF8_BOM):
        return raw[len(_UTF8_BOM) :].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


@dataclass(frozen=True, slots=True)
class BankSignatureBlock:
    """Блок ЭП банка в конце файла: подписано всё, что идёт до СекцияПодписьБанка.

    В формате 1С подписи нет, это соглашение синтетики для MockSignatureAdapter.
    """

    signer: str
    content_hash: str
    signed_content: bytes

    @classmethod
    def find(cls, raw: bytes) -> Self | None:
        text = decode_1c(raw)
        start = text.find(SIGNATURE_START)
        if start == -1:
            return None
        fields = _key_values(text[start:].splitlines()[1:], stop=SIGNATURE_END)
        # Хеш считается по исходным байтам: перекодировка могла бы скрыть подмену символов.
        encoding = "utf-8" if _is_utf8(raw) else "cp1251"
        prefix = raw.find(SIGNATURE_START.encode(encoding))
        return cls(
            signer=fields.get("Подписант", ""),
            content_hash=fields.get("ХешДокумента", "").removeprefix("sha256:"),
            signed_content=raw[:prefix],
        )


def _is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _key_values(lines: list[str], stop: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line == stop:
            break
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    return fields


class Statement1CParser(StatementParser):
    def parse(self, raw: bytes) -> ParsedStatement:
        try:
            text = decode_1c(raw)
        except UnicodeDecodeError:
            _fail("Файл не в кодировке Windows-1251 или UTF-8")
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines or lines[0] != FORMAT_MARKER:
            _fail("Это не выписка 1С: нет заголовка 1CClientBankExchange")

        header: dict[str, str] = {}
        documents: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        finished = False
        for line in lines[1:]:
            if line == "КонецФайла":
                finished = True
                break
            if line.startswith("СекцияДокумент"):
                if current is not None:
                    _fail("Документ не закрыт строкой КонецДокумента")
                current = {}
                continue
            if line == "КонецДокумента":
                if current is None:
                    _fail("КонецДокумента без начала документа")
                documents.append(current)
                current = None
                continue
            if line in ("СекцияРасчСчет", "КонецРасчСчет"):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                _fail(f"Строка без знака «=»: {line[:40]}")
            # Поля СекцияРасчСчет идут после заголовка и уточняют его даты и счёт.
            target = current if current is not None else header
            target[key] = value.strip()
        if current is not None:
            _fail("Документ не закрыт строкой КонецДокумента")
        if not finished:
            _fail("Файл обрезан: нет строки КонецФайла")

        account = header.get("РасчСчет") or header.get("РасчетныйСчет")
        if not account:
            _fail("В заголовке нет расчётного счёта")
        period = _period(header)
        if not documents:
            _fail("В выписке нет операций")
        owner_inn, operations = _operations(documents, account, period)
        return ParsedStatement(
            bank_name=header.get("Отправитель", ""),
            account=account,
            account_inn=owner_inn,
            period=period,
            operations=operations,
        )


def _period(header: dict[str, str]) -> Period:
    start = _date(header.get("ДатаНачала"), "ДатаНачала")
    end = _date(header.get("ДатаКонца"), "ДатаКонца")
    try:
        return Period(start, end)
    except ValueError:
        _fail("ДатаКонца раньше ДатаНачала")


def _operations(
    documents: list[dict[str, str]], account: str, period: Period
) -> tuple[str, tuple[StatementOperation, ...]]:
    owner_inns: set[str] = set()
    operations: list[StatementOperation] = []
    for number, doc in enumerate(documents, start=1):
        # Направление определяем по счёту, а не по ИНН: у ИП бывают переводы между своими счетами.
        if doc.get("ПолучательСчет") == account:
            direction, owner_key, counterparty_key = (
                Direction.INCOMING,
                "ПолучательИНН",
                "ПлательщикИНН",
            )
        elif doc.get("ПлательщикСчет") == account:
            direction, owner_key, counterparty_key = (
                Direction.OUTGOING,
                "ПлательщикИНН",
                "ПолучательИНН",
            )
        else:
            _fail(f"Документ {number}: операция не по счёту выписки")
        owner_inns.add(doc.get(owner_key, ""))
        op_date = _date(doc.get("Дата"), f"Дата документа {number}")
        if not period.date_from <= op_date <= period.date_to:
            _fail(f"Документ {number}: дата {op_date:%d.%m.%Y} вне периода выписки")
        operations.append(
            StatementOperation(
                date=op_date,
                amount=_amount(doc.get("Сумма"), number),
                direction=direction,
                counterparty_inn=doc.get(counterparty_key, ""),
            )
        )
    if len(owner_inns) != 1 or not next(iter(owner_inns)):
        _fail("ИНН владельца счёта в документах выписки не совпадает")
    return owner_inns.pop(), tuple(operations)


def _date(value: str | None, field: str) -> date:
    if not value:
        _fail(f"Нет поля {field}")
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()  # noqa: DTZ007 дата без времени и зоны
    except ValueError:
        _fail(f"{field}: дата не в формате ДД.ММ.ГГГГ")


def _amount(value: str | None, number: int) -> Money:
    try:
        amount = Decimal((value or "").replace(",", "."))
    except InvalidOperation:
        _fail(f"Документ {number}: сумма не число")
    if not amount.is_finite() or amount <= 0:
        _fail(f"Документ {number}: сумма должна быть положительной")
    return Money(amount)


def _fail(message: str) -> Never:
    raise ValidationFailed([FieldError(field="file", code="STATEMENT_FORMAT", message=message)])
