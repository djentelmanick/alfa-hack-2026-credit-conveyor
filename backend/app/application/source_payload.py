"""Ответы источников в снимке заявки: JSON для раздела «что получили сами» отчёта аналитика.

Типизированные ответы конвейер не хранит, поэтому отчёт читает обороты обратно из снимка.
Запись и чтение лежат рядом, чтобы формат менялся в одном месте.
"""

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.domain.value_objects import Money, MonthlyTurnover, MonthRevenue, Payer

type JsonValue = str | int | bool | list[JsonValue] | dict[str, JsonValue] | None


def to_payload(value: object) -> dict[str, JsonValue]:
    data = _json(value)
    return data if isinstance(data, dict) else {"value": data}


def _json(value: object) -> JsonValue:
    match value:
        case None | bool() | int() | str():
            return value
        case Money():
            return str(value)
        case Decimal():
            return format(value, "f")
        case Enum():
            return _json(value.value)
        case datetime() | date():
            return value.isoformat()
        case Mapping():
            return {str(k): _json(v) for k, v in value.items()}
        case list() | tuple():
            return [_json(v) for v in value]
        case _ if is_dataclass(value) and not isinstance(value, type):
            return {f.name: _json(getattr(value, f.name)) for f in fields(value)}
        case float():
            return format(Decimal(str(value)), "f")
    raise TypeError(f"Нельзя сохранить в снимок значение типа {type(value).__name__}")


def turnover_from_payload(payload: Mapping[str, object]) -> MonthlyTurnover | None:
    """None, если счёта в нашем банке нет или снимок не в формате to_payload(MonthlyTurnover)."""
    inn = payload.get("inn")
    months = payload.get("months")
    if not isinstance(inn, str) or not isinstance(months, Sequence):
        return None
    return MonthlyTurnover(
        inn=inn, months=tuple(_month(m) for m in months if isinstance(m, Mapping))
    )


def _month(raw: Mapping[str, object]) -> MonthRevenue:
    payers = raw.get("top_payers")
    return MonthRevenue(
        month=date.fromisoformat(str(raw["month"])),
        incoming=Money.of(str(raw["incoming"])),
        outgoing=Money.of(str(raw.get("outgoing", "0"))),
        top_payers=tuple(
            Payer(
                inn=str(p["inn"]),
                name=str(p["name"]),
                amount=Money.of(str(p["amount"])),
                share_pct=Decimal(str(p["share_pct"])),
            )
            for p in (payers if isinstance(payers, Sequence) else ())
            if isinstance(p, Mapping)
        ),
    )
