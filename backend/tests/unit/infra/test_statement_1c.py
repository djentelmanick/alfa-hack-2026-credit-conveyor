from datetime import date

import pytest

from app.domain.enums import Direction
from app.domain.errors import ValidationFailed
from app.domain.value_objects import Money, Period
from app.infra.parsers.statement_1c import Statement1CParser
from tests.unit.infra.conftest import IVANOVA

ACCOUNT = "40802810400000012345"


def _statement(*documents: str, header: str | None = None, footer: str = "КонецФайла") -> str:
    head = header or (
        "1CClientBankExchange\nВерсияФормата=1.03\nКодировка=Windows\n"
        'Отправитель=АО "СЕВЕРНЫЙ БАНК"\n'
        f"ДатаНачала=01.09.2026\nДатаКонца=30.09.2026\nРасчСчет={ACCOUNT}\n"
    )
    return head + "".join(documents) + footer + "\n"


def _document(
    day: str = "10.09.2026",
    amount: str = "1000.00",
    payer_account: str = "30233810100000000777",
    payer_inn: str = "7702987655",
    receiver_account: str = ACCOUNT,
    receiver_inn: str = IVANOVA,
) -> str:
    return (
        "СекцияДокумент=Платежное поручение\nНомер=1\n"
        f"Дата={day}\nСумма={amount}\n"
        f"ПлательщикСчет={payer_account}\nПлательщикИНН={payer_inn}\n"
        f"ПолучательСчет={receiver_account}\nПолучательИНН={receiver_inn}\n"
        "КонецДокумента\n"
    )


def _parse(text: str, encoding: str = "cp1251") -> object:
    return Statement1CParser().parse(text.encode(encoding))


class TestScenarioFourStatements:
    def test_four_month_statement(self, statement_4m: bytes) -> None:
        parsed = Statement1CParser().parse(statement_4m)

        assert parsed.account_inn == IVANOVA
        assert parsed.account == ACCOUNT
        assert parsed.bank_name == 'АО "СЕВЕРНЫЙ БАНК"'
        assert parsed.period == Period(date(2026, 6, 24), date(2026, 10, 23))
        assert parsed.months_covered == 4
        assert len(parsed.operations) == 143
        assert len(parsed.incoming()) == 122
        assert parsed.last_operation_date == date(2026, 10, 23)
        assert parsed.annual_revenue() == Money.of(15_000_000)

    def test_twelve_month_statement(self, statement_12m: bytes) -> None:
        parsed = Statement1CParser().parse(statement_12m)

        assert parsed.account_inn == IVANOVA
        assert parsed.period == Period(date(2025, 10, 24), date(2026, 10, 23))
        assert parsed.months_covered == 12
        assert len(parsed.operations) == 429
        assert len(parsed.incoming()) == 365
        assert parsed.last_operation_date == date(2026, 10, 23)
        assert parsed.annual_revenue() == Money.of(15_000_000)

    def test_twelve_month_revenue_is_even_by_quarter(self, statement_12m: bytes) -> None:
        months = Statement1CParser().parse(statement_12m).monthly_turnover().months
        full = [m.incoming.amount for m in months[1:-1]]

        # «Ровная по кварталам» (SCOPE, раздел 12): полные месяцы отличаются меньше чем на 15%.
        assert max(full) / min(full) < 1.15
        assert sum((m.incoming for m in months), Money.zero()) == Money.of(15_000_000)


class TestOperations:
    def test_direction_by_account(self) -> None:
        text = _statement(
            _document(amount="500.00"),
            _document(
                amount="200.00",
                payer_account=ACCOUNT,
                payer_inn=IVANOVA,
                receiver_account="40702810900000054321",
                receiver_inn="7720123457",
            ),
        )

        parsed = Statement1CParser().parse(text.encode("cp1251"))

        assert [(op.direction, op.counterparty_inn) for op in parsed.operations] == [
            (Direction.INCOMING, "7702987655"),
            (Direction.OUTGOING, "7720123457"),
        ]
        assert parsed.monthly_turnover().total() == Money.of(500)

    def test_utf8_file(self) -> None:
        parsed = Statement1CParser().parse(_statement(_document()).encode("utf-8"))
        assert parsed.account_inn == IVANOVA

    def test_utf8_with_bom(self) -> None:
        raw = b"\xef\xbb\xbf" + _statement(_document()).encode("utf-8")
        assert Statement1CParser().parse(raw).account_inn == IVANOVA

    def test_crlf_and_comma_decimal(self) -> None:
        text = _statement(_document(amount="1234,50")).replace("\n", "\r\n")
        parsed = Statement1CParser().parse(text.encode("cp1251"))
        assert parsed.operations[0].amount == Money.of("1234.50")

    def test_account_section_overrides_header(self) -> None:
        header = (
            "1CClientBankExchange\nДатаНачала=01.01.2026\nДатаКонца=30.09.2026\n"
            f"РасчСчет={ACCOUNT}\nСекцияРасчСчет\nДатаНачала=01.09.2026\n"
            f"ДатаКонца=30.09.2026\nРасчСчет={ACCOUNT}\nКонецРасчСчет\n"
        )
        parsed = Statement1CParser().parse(_statement(_document(), header=header).encode("cp1251"))
        assert parsed.period == Period(date(2026, 9, 1), date(2026, 9, 30))


class TestInvalidFiles:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "нет заголовка"),
            ("Какой-то текст\n", "нет заголовка"),
            (_statement(_document(), footer=""), "нет строки КонецФайла"),
            (_statement(_document().replace("КонецДокумента\n", "")), "не закрыт"),
            (_statement("КонецДокумента\n"), "без начала"),
            (_statement(), "нет операций"),
            (_statement(_document(amount="много")), "сумма не число"),
            (_statement(_document(amount="-5.00")), "положительной"),
            (_statement(_document(day="2026-09-10")), "ДД.ММ.ГГГГ"),
            (_statement(_document(day="10.10.2026")), "вне периода"),
            (_statement(_document(receiver_account="40702810000000000000")), "не по счёту"),
            (
                _statement(_document(), _document(receiver_inn="770498765403")),
                "не совпадает",
            ),
            (
                _statement(
                    _document(),
                    header="1CClientBankExchange\nДатаНачала=01.09.2026\nДатаКонца=30.09.2026\n",
                ),
                "расчётного счёта",
            ),
            (
                _statement(
                    _document(),
                    header=f"1CClientBankExchange\nДатаНачала=30.09.2026\n"
                    f"ДатаКонца=01.09.2026\nРасчСчет={ACCOUNT}\n",
                ),
                "раньше",
            ),
            (
                _statement(_document()).replace("Кодировка=Windows", "Кодировка Windows"),
                "без знака",
            ),
        ],
    )
    def test_rejects(self, text: str, message: str) -> None:
        with pytest.raises(ValidationFailed) as exc_info:
            _parse(text)

        [error] = exc_info.value.details
        assert error.field == "file"
        assert error.code == "STATEMENT_FORMAT"
        assert message in error.message
