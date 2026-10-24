import calendar
from datetime import date


def add_months(day: date, months: int) -> date:
    """Сдвиг на месяцы с прижатием к концу месяца: 31 января + 1 месяц = 28 (29) февраля."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def full_months_between(start: date, end: date) -> int:
    """Число полных месяцев от start до end; 0, если end раньше start."""
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if add_months(start, months) > end:
        months -= 1
    return months
