from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.api.v1.schemas.errors import ErrorOut


class RequestModel(BaseModel):
    # Лишние поля отклоняются: в теле нельзя подсунуть, например, role или user_id.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _rate(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if "." in text else f"{text}.0"


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


# Деньги и ставки уходят в JSON строками (API.md, раздел 1), чтобы фронт не терял копейки на float.
MoneyStr = Annotated[Decimal, PlainSerializer(_money, return_type=str)]
RateStr = Annotated[Decimal, PlainSerializer(_rate, return_type=str)]
Percent = Annotated[Decimal, PlainSerializer(_number, return_type=int | float)]

INN_PATTERN = r"^([0-9]{10}|[0-9]{12})$"

AUTH_ERRORS: dict[int | str, dict[str, object]] = {401: {"model": ErrorOut}}
ROLE_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorOut},
    403: {"model": ErrorOut},
    422: {"model": ErrorOut},
}
