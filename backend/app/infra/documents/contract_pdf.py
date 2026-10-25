"""PDF договора на reportlab.

Шрифт DejaVu лежит рядом с кодом: стандартные шрифты PDF не умеют кириллицу,
а системных шрифтов в slim-образе нет.
"""

import asyncio
import io
import threading
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.domain.entities import CreditLineContract, LoanContract, ScheduleItem
from app.domain.entities.contract import ContractChange, format_rate
from app.domain.enums import CollateralType, Variant
from app.domain.ports import ContractDocumentData, ContractDocumentRenderer
from app.domain.value_objects import Money

FONTS_DIR = Path(__file__).parent / "fonts"
FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

_VARIANT_LABELS = {Variant.UNSECURED: "без залога", Variant.SECURED: "с залогом"}
_COLLATERAL_LABELS = {
    CollateralType.REAL_ESTATE: "недвижимость",
    CollateralType.VEHICLE: "транспорт",
    CollateralType.EQUIPMENT: "оборудование",
    CollateralType.GOODS: "товары в обороте",
}
_FIELD_LABELS = {"rate": "Ставка, % годовых", "term_months": "Срок, мес"}

_fonts_lock = threading.Lock()


def _register_fonts() -> None:
    # Реестр шрифтов reportlab общий на процесс, а сборка идёт в потоках.
    with _fonts_lock:
        if FONT in pdfmetrics.getRegisteredFontNames():
            return
        pdfmetrics.registerFont(TTFont(FONT, str(FONTS_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(
            FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD
        )


def _money(value: Money) -> str:
    whole, _, kopecks = f"{value.amount:.2f}".partition(".")
    return f"{int(whole):,}".replace(",", " ") + f",{kopecks} руб."


def _date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


class ReportlabContractRenderer(ContractDocumentRenderer):
    def __init__(self) -> None:
        _register_fonts()
        self._text = ParagraphStyle("text", fontName=FONT, fontSize=10, leading=14)
        self._title = ParagraphStyle(
            "title", parent=self._text, fontName=FONT_BOLD, fontSize=14, leading=18
        )
        self._heading = ParagraphStyle(
            "heading", parent=self._text, fontName=FONT_BOLD, fontSize=11, spaceBefore=8
        )

    async def render(self, data: ContractDocumentData) -> bytes:
        # Сборка PDF занимает процессор: в event loop она задержала бы остальные запросы.
        return await asyncio.to_thread(self._render, data)

    def _render(self, data: ContractDocumentData) -> bytes:
        buffer = io.BytesIO()
        contract = data.contract
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"Кредитный договор {contract.number}",
            author="Кредитный конвейер МСБ",
        )
        document.build(self._story(data))
        return buffer.getvalue()

    def _story(self, data: ContractDocumentData) -> list[Any]:
        contract = data.contract
        version = data.version
        story: list[Any] = [self._para(f"Кредитный договор № {contract.number}", self._title)]
        if version.version_no > 1:
            story.append(
                self._para(f"Дополнительное соглашение, версия {version.version_no}", self._heading)
            )
        story.append(Spacer(1, 4 * mm))
        story.append(self._terms_table(data))

        if version.changes:
            story.append(self._para("Что изменилось", self._heading))
            story.append(self._changes_table(version.changes))
        if version.schedule:
            story.append(self._para("График платежей", self._heading))
            story.append(self._schedule_table(version.schedule))
        elif isinstance(contract, CreditLineContract):
            story.append(
                self._para(
                    "Проценты начисляются ежемесячно на непогашенные транши, "
                    "основной долг по траншу возвращается в конце его срока.",
                    self._text,
                )
            )

        story.append(Spacer(1, 6 * mm))
        signed_at = version.created_at.strftime("%d.%m.%Y %H:%M UTC")
        story.append(
            self._para(
                f"Версия {version.version_no} от {signed_at}. "
                "Подписано простой электронной подписью (код подтверждения).",
                self._text,
            )
        )
        return story

    def _terms_table(self, data: ContractDocumentData) -> Table:
        contract = data.contract
        rows: list[tuple[str, str]] = [
            ("Заёмщик", data.company_name),
            ("ИНН", data.inn),
            ("Продукт", f"{data.product_name}, {_VARIANT_LABELS[contract.variant]}"),
        ]
        match contract:
            case LoanContract():
                rows.append(("Сумма кредита", _money(contract.principal)))
            case CreditLineContract():
                rows.append(("Лимит", _money(contract.limit)))
        rows += [
            ("Ставка", f"{format_rate(contract.rate)}% годовых"),
            ("Срок", f"{contract.term_months} мес"),
            ("Дата начала", _date(contract.start_date)),
            ("Дата окончания", _date(contract.end_date)),
        ]
        if data.collateral is not None:
            collateral = data.collateral
            rows.append(
                (
                    "Залог",
                    f"{_COLLATERAL_LABELS[collateral.type]}: {collateral.description}, "
                    f"оценка {_money(collateral.estimated_value)}",
                )
            )
        return self._table(
            [[self._para(k, self._text), self._para(v, self._text)] for k, v in rows],
            col_widths=[45 * mm, 125 * mm],
            header=False,
        )

    def _changes_table(self, changes: tuple[ContractChange, ...]) -> Table:
        rows = [["Условие", "Было", "Стало"]]
        rows += [[_FIELD_LABELS.get(c.field, c.field), c.old, c.new] for c in changes]
        return self._table(rows, col_widths=[70 * mm, 50 * mm, 50 * mm])

    def _schedule_table(self, schedule: tuple[ScheduleItem, ...]) -> Table:
        rows = [["№", "Дата", "Платёж", "Основной долг", "Проценты", "Остаток"]]
        rows += [
            [
                str(n),
                _date(item.due_date),
                _money(item.payment),
                _money(item.principal),
                _money(item.interest),
                _money(item.balance),
            ]
            for n, item in enumerate(schedule, start=1)
        ]
        table = self._table(
            rows, col_widths=[10 * mm, 22 * mm, 35 * mm, 35 * mm, 33 * mm, 35 * mm], font_size=8
        )
        table.setStyle(TableStyle([("ALIGN", (2, 1), (-1, -1), "RIGHT")]))
        return table

    def _table(
        self,
        rows: list[list[Any]],
        *,
        col_widths: list[float],
        header: bool = True,
        font_size: int = 9,
    ) -> Table:
        table = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
        style = [
            ("FONTNAME", (0, 0), (-1, -1), FONT),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            style += [
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ]
        table.setStyle(TableStyle(style))
        return table

    @staticmethod
    def _para(text: str, style: ParagraphStyle) -> Paragraph:
        # Paragraph разбирает разметку: кавычки и амперсанды в названии компании её ломают.
        return Paragraph(escape(text), style)
