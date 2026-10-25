"""Полномочия заявителя: кто может подать и подписать заявку от имени компании."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.domain.enums import ApplicantRole, AuthorityBasis, PoaKind, PoaPower
from app.domain.errors import FieldError
from app.domain.value_objects import CompanyProfile

POA_FIELD = "authority.power_of_attorney"

# Оба полномочия проверяются при подаче: без подписания заявка дошла бы до одобрения
# и упёрлась в тупик, кабинета директора у нас нет.
REQUIRED_POWERS: frozenset[PoaPower] = frozenset(PoaPower)

POWER_LABELS: dict[PoaPower, str] = {
    PoaPower.LOAN_APPLICATION: "подача заявки на кредит",
    PoaPower.LOAN_AGREEMENT_SIGNING: "подписание кредитного договора",
}


@dataclass(frozen=True, kw_only=True, slots=True)
class PoaRequest:
    """Реквизиты доверенности, которые вводит заявитель."""

    kind: PoaKind
    mchd_guid: UUID | None = None
    notarized_on: date | None = None
    register_number: str | None = None
    notary_name: str | None = None

    def describe(self) -> str:
        if self.kind is PoaKind.MCHD:
            return f"МЧД {self.mchd_guid}"
        notarized = self.notarized_on.strftime("%d.%m.%Y") if self.notarized_on else ""
        return f"нотариальная от {notarized} № {self.register_number}, нотариус {self.notary_name}"


@dataclass(frozen=True, kw_only=True, slots=True)
class PowerOfAttorney:
    """Запись реестра доверенностей (ФНС для МЧД, ФНП для нотариальных)."""

    kind: PoaKind
    principal_inn: str
    signed_by_inn: str
    signed_by_name: str
    representative_inn: str
    valid_until: date
    revoked: bool
    powers: frozenset[PoaPower]


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicantAuthority:
    """Подтверждённое основание, по которому заявитель действует от имени компании."""

    role: ApplicantRole
    basis: AuthorityBasis
    representative_inn: str
    representative_name: str
    poa: PoaRequest | None = None
    registered_founder: bool | None = None


def check_power_of_attorney(
    poa: PowerOfAttorney | None,
    *,
    company: CompanyProfile,
    representative_inn: str,
    today: date,
) -> tuple[FieldError, ...]:
    """Все нарушения разом, чтобы заявитель увидел полный список проблем доверенности."""
    if poa is None:
        return (
            FieldError(
                POA_FIELD, "POA_NOT_FOUND", "Доверенность не найдена в реестре. Проверьте реквизиты"
            ),
        )
    errors: list[FieldError] = []
    if poa.revoked:
        errors.append(FieldError(POA_FIELD, "POA_REVOKED", "Доверенность отозвана"))
    if poa.valid_until < today:
        errors.append(
            FieldError(
                POA_FIELD,
                "POA_EXPIRED",
                f"Срок доверенности истёк {poa.valid_until.strftime('%d.%m.%Y')}",
            )
        )
    if poa.principal_inn != company.inn:
        errors.append(
            FieldError(POA_FIELD, "POA_WRONG_PRINCIPAL", "Доверенность выдана другой компанией")
        )
    elif poa.signed_by_inn != company.director_inn:
        errors.append(
            FieldError(
                POA_FIELD,
                "POA_NOT_SIGNED_BY_DIRECTOR",
                f"Доверенность подписал не действующий руководитель по ЕГРЮЛ ({company.director})",
            )
        )
    if poa.representative_inn != representative_inn:
        errors.append(
            FieldError(
                POA_FIELD, "POA_WRONG_REPRESENTATIVE", "Доверенность выдана другому человеку"
            )
        )
    missing = [POWER_LABELS[p] for p in PoaPower if p in REQUIRED_POWERS - poa.powers]
    if missing:
        errors.append(
            FieldError(
                POA_FIELD,
                "POA_MISSING_POWER",
                f"В доверенности нет полномочия: {', '.join(missing)}",
            )
        )
    return tuple(errors)
