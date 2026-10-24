from datetime import date
from decimal import Decimal

from app.domain.dates import add_months
from app.domain.entities.contract import ScheduleItem
from app.domain.value_objects import Money


class AnnuityCalculator:
    """Аннуитет: равные платежи, ставка годовая в процентах, проценты начисляются помесячно."""

    @staticmethod
    def monthly_rate(annual_rate_pct: Decimal) -> Decimal:
        return annual_rate_pct / 1200

    @classmethod
    def payment(cls, amount: Money, term_months: int, annual_rate_pct: Decimal) -> Money:
        if term_months <= 0:
            raise ValueError("Срок должен быть положительным")
        if annual_rate_pct < 0:
            raise ValueError("Ставка не может быть отрицательной")
        rate = cls.monthly_rate(annual_rate_pct)
        if rate == 0:
            return amount / term_months
        return Money(amount.amount * rate / (1 - (1 + rate) ** -term_months))

    @classmethod
    def schedule(
        cls, amount: Money, term_months: int, annual_rate_pct: Decimal, start: date
    ) -> list[ScheduleItem]:
        payment = cls.payment(amount, term_months, annual_rate_pct)
        rate = cls.monthly_rate(annual_rate_pct)
        balance = amount
        items: list[ScheduleItem] = []
        for month in range(1, term_months + 1):
            interest = Money(balance.amount * rate)
            # Округления копеек накапливаются, поэтому последний платёж добирает остаток долга.
            principal = balance if month == term_months else payment - interest
            balance -= principal
            items.append(
                ScheduleItem(
                    due_date=add_months(start, month),
                    principal=principal,
                    interest=interest,
                    balance=balance,
                )
            )
        return items
