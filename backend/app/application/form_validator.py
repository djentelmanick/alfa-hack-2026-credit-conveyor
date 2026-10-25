"""Проверки формы перед отправкой заявки (SCOPE раздел 4, этап 1; BACKEND_SPEC раздел 6)."""

import re
from dataclasses import dataclass
from typing import ClassVar

from app.domain.entities import Application, CheckResult, User
from app.domain.enums import CheckOutcome, CheckStage, CompanyKind, ConsentType, Variant
from app.domain.errors import (
    ActiveApplicationExists,
    FieldError,
    NotDirector,
    NotFound,
    ValidationFailed,
)
from app.domain.labels import APPLICATION_STATUS_LABELS, CONSENT_TYPE_LABELS
from app.domain.ports import AccountTurnoverPort, ApplicationRepository, CompanyRegistryPort
from app.domain.products import LoanProduct, ProductCatalog

_INN_LENGTH = {CompanyKind.LLC: 10, CompanyKind.IE: 12}
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True, slots=True)
class _Check:
    result: CheckResult
    errors: tuple[FieldError, ...] = ()


def _check(code: str, expected: str, actual: str, errors: tuple[FieldError, ...] = ()) -> _Check:
    return _Check(
        CheckResult(
            code=code,
            stage=CheckStage.FORM,
            expected=expected,
            actual=actual,
            outcome=CheckOutcome.FAILED if errors else CheckOutcome.PASSED,
        ),
        errors,
    )


class FormValidator:
    """Собирает все ошибки полей разом, чтобы клиент исправил форму за один заход.

    Чужая компания и дубль заявки это не ошибки полей: по API.md у них свои коды 403 и 409.
    """

    LLC_CONSENTS: ClassVar[frozenset[ConsentType]] = frozenset(
        {ConsentType.BKI_COMPANY, ConsentType.BKI_PERSONAL, ConsentType.PERSONAL_DATA}
    )
    STATEMENT_LOOKBACK_MONTHS: ClassVar[int] = 12

    def __init__(
        self,
        catalog: ProductCatalog,
        registry: CompanyRegistryPort,
        turnover: AccountTurnoverPort,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._turnover = turnover

    async def validate(
        self, application: Application, applicant: User, applications: ApplicationRepository
    ) -> list[CheckResult]:
        product = self._product(application)
        director = await self._applicant_is_director(application, applicant)
        active = await self._no_active_application(application, applications)
        checks = [
            self._form_complete(application),
            *self._terms_in_range(application, product),
            self._purpose_allowed(application, product),
            director,
            active,
            self._collateral_coverage(application, product),
            self._owner_guarantee(application),
            self._consents_given(application),
            await self._statement_uploaded(application),
            self._statement_inn_match(application),
        ]
        if not director.result.passed:
            raise NotDirector()
        if not active.result.passed:
            raise ActiveApplicationExists()
        errors = [error for check in checks for error in check.errors]
        if errors:
            raise ValidationFailed(errors)
        return [check.result for check in checks]

    def _product(self, application: Application) -> LoanProduct:
        try:
            return self._catalog.get(application.product_code)
        except NotFound:
            raise ValidationFailed(
                [FieldError("product_code", "FORM_COMPLETE", "Такого продукта нет")]
            ) from None

    def _form_complete(self, application: Application) -> _Check:
        errors: list[FieldError] = []
        length = _INN_LENGTH[application.company_kind]
        if len(application.inn) != length or not _DIGITS.fullmatch(application.inn):
            errors.append(
                FieldError("inn", "FORM_COMPLETE", f"ИНН должен состоять из {length} цифр")
            )
        if not application.amount.is_positive():
            errors.append(FieldError("amount", "FORM_COMPLETE", "Укажите сумму"))
        if application.term_months <= 0:
            errors.append(FieldError("term_months", "FORM_COMPLETE", "Укажите срок"))
        collateral = application.collateral
        if collateral is not None and not collateral.description.strip():
            errors.append(FieldError("collateral.description", "FORM_COMPLETE", "Опишите залог"))
        actual = "поля заполнены" if not errors else ", ".join(e.field for e in errors)
        return _check(
            "FORM_COMPLETE", "все обязательные поля в верном формате", actual, tuple(errors)
        )

    def _terms_in_range(self, application: Application, product: LoanProduct) -> list[_Check]:
        amount_check, term_check = product.validate_terms(
            application.variant, application.amount, application.term_months
        )
        return [
            _Check(
                result,
                ()
                if result.passed
                else (FieldError(field, result.code, f"Нужно: {result.expected}"),),
            )
            for result, field in ((amount_check, "amount"), (term_check, "term_months"))
        ]

    def _purpose_allowed(self, application: Application, product: LoanProduct) -> _Check:
        allowed = self._catalog.is_allowed(application.purpose, application.product_code)
        errors = (
            ()
            if allowed
            else (
                FieldError(
                    "product_code", "PURPOSE_ALLOWED", "Этот продукт не подходит под выбранную цель"
                ),
            )
        )
        return _check(
            "PURPOSE_ALLOWED",
            f"продукт допустим для цели «{self._catalog.purpose_label(application.purpose)}»",
            product.name,
            errors,
        )

    async def _applicant_is_director(self, application: Application, applicant: User) -> _Check:
        expected = "заявитель руководитель компании по ЕГРЮЛ или сам ИП"
        try:
            company = await self._registry.find_by_inn(application.inn)
        except NotFound:
            return _check(
                "APPLICANT_IS_DIRECTOR",
                expected,
                "ИНН не найден в ЕГРЮЛ/ЕГРИП",
                (FieldError("inn", "APPLICANT_IS_DIRECTOR", "ИНН не найден"),),
            )
        if company.is_director(applicant):
            return _check("APPLICANT_IS_DIRECTOR", expected, company.director)
        return _check(
            "APPLICANT_IS_DIRECTOR",
            expected,
            f"руководитель по ЕГРЮЛ {company.director}",
            (FieldError("inn", "APPLICANT_IS_DIRECTOR", NotDirector.default_message),),
        )

    async def _no_active_application(
        self, application: Application, applications: ApplicationRepository
    ) -> _Check:
        existing = await applications.find_active_by_inn(application.inn)
        if existing is None or existing.id == application.id:
            return _check("NO_ACTIVE_APPLICATION", "нет другой активной заявки", "нет")
        return _check(
            "NO_ACTIVE_APPLICATION",
            "нет другой активной заявки",
            f"заявка {existing.id} в статусе «{APPLICATION_STATUS_LABELS[existing.status]}»",
            (FieldError("inn", "NO_ACTIVE_APPLICATION", ActiveApplicationExists.default_message),),
        )

    def _collateral_coverage(self, application: Application, product: LoanProduct) -> _Check:
        if application.variant is not Variant.SECURED:
            return _check("COLLATERAL_COVERAGE", "залог не нужен", "вариант без залога")
        min_pct = product.variant(Variant.SECURED).collateral_coverage_min_pct
        expected = f"оценка залога не меньше {min_pct}% суммы"
        collateral = application.collateral
        if collateral is None:
            return _check(
                "COLLATERAL_COVERAGE",
                expected,
                "залог не указан",
                (FieldError("collateral", "COLLATERAL_COVERAGE", "Укажите залог"),),
            )
        actual = f"оценка {collateral.estimated_value} при сумме {application.amount}"
        if min_pct is None or collateral.covers(application.amount, min_pct):
            return _check("COLLATERAL_COVERAGE", expected, actual)
        return _check(
            "COLLATERAL_COVERAGE",
            expected,
            actual,
            (
                FieldError(
                    "collateral.estimated_value",
                    "COLLATERAL_COVERAGE",
                    f"Оценка залога должна покрывать не меньше {min_pct}% суммы",
                ),
            ),
        )

    def _owner_guarantee(self, application: Application) -> _Check:
        required = (
            application.company_kind is CompanyKind.LLC and application.variant is Variant.UNSECURED
        )
        if not required:
            return _check("OWNER_GUARANTEE", "поручительство не нужно", "не требуется")
        if application.owner_guarantee:
            return _check("OWNER_GUARANTEE", "согласие собственника на поручительство", "дано")
        return _check(
            "OWNER_GUARANTEE",
            "согласие собственника на поручительство",
            "не дано",
            (
                FieldError(
                    "owner_guarantee",
                    "OWNER_GUARANTEE",
                    "Для ООО без залога нужно поручительство собственника",
                ),
            ),
        )

    def _consents_given(self, application: Application) -> _Check:
        given = {c.type for c in application.consents}
        if application.company_kind is CompanyKind.LLC:
            expected = "согласия БКИ компании и руководителя, согласие на ПДн"
            ok = given >= self.LLC_CONSENTS
        else:
            # У ИП кредитная история одна, поэтому достаточно одного согласия БКИ любого типа.
            expected = "согласие БКИ и согласие на ПДн"
            ok = ConsentType.PERSONAL_DATA in given and bool(
                given & {ConsentType.BKI_COMPANY, ConsentType.BKI_PERSONAL}
            )
        actual = ", ".join(CONSENT_TYPE_LABELS[c] for c in sorted(given)) or "нет согласий"
        errors = () if ok else (FieldError("consents", "CONSENTS_GIVEN", "Дайте все согласия"),)
        return _check("CONSENTS_GIVEN", expected, actual, errors)

    async def _statement_uploaded(self, application: Application) -> _Check:
        turnover = await self._turnover.monthly_turnover(
            application.inn, self.STATEMENT_LOOKBACK_MONTHS
        )
        expected = "есть счёт в банке или загружена выписка"
        if turnover is not None:
            return _check("STATEMENT_UPLOADED", expected, "счёт в банке есть")
        if application.statements:
            return _check("STATEMENT_UPLOADED", expected, "выписка загружена")
        return _check(
            "STATEMENT_UPLOADED",
            expected,
            "счёта нет, выписка не загружена",
            (FieldError("statements", "STATEMENT_UPLOADED", "Загрузите выписку из своего банка"),),
        )

    def _statement_inn_match(self, application: Application) -> _Check:
        expected = f"ИНН в выписке {application.inn}"
        foreign = sorted({s.account_inn for s in application.statements} - {application.inn})
        if not application.statements:
            return _check("STATEMENT_INN_MATCH", expected, "выписки нет")
        if not foreign:
            return _check("STATEMENT_INN_MATCH", expected, application.inn)
        return _check(
            "STATEMENT_INN_MATCH",
            expected,
            ", ".join(foreign),
            (FieldError("statements", "STATEMENT_INN_MATCH", "Выписка по счёту другого ИНН"),),
        )
