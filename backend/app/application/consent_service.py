from collections.abc import Callable, Sequence

from app.domain.entities import Consent, TokenClaims, UserCompany
from app.domain.enums import CompanyKind, ConsentType
from app.domain.errors import FieldError, NotDirector, ValidationFailed
from app.domain.ports import Clock, CompanyRegistryPort, UnitOfWork

# Согласие от имени компании даёт только ООО: ИП отвечает по БКИ как физлицо (SCOPE, раздел 3).
_APPLICABLE: dict[CompanyKind, frozenset[ConsentType]] = {
    CompanyKind.LLC: frozenset(ConsentType),
    CompanyKind.IE: frozenset({ConsentType.BKI_PERSONAL, ConsentType.PERSONAL_DATA}),
}


class ConsentService:
    def __init__(
        self, *, uow: Callable[[], UnitOfWork], registry: CompanyRegistryPort, clock: Clock
    ) -> None:
        self._uow = uow
        self._registry = registry
        self._clock = clock

    async def give(
        self, claims: TokenClaims, inn: str, types: Sequence[ConsentType]
    ) -> list[Consent]:
        """Уже данное согласие не переписывается: возвращается с исходным временем."""
        company = await self._registry.find_by_inn(inn)
        async with self._uow() as uow:
            user = await uow.users.get(claims.user_id)
            is_director = company.is_director(user)
            if not is_director and not user.represents(company.inn):
                raise NotDirector()
            _ensure_applicable(company.kind, types)

            given = {c.type: c for c in await uow.consents.list_for(inn, user.id)}
            now = self._clock.now()
            result: list[Consent] = []
            for consent_type in dict.fromkeys(types):
                consent = given.get(consent_type)
                if consent is None:
                    consent = Consent(type=consent_type, given_by=user.id, given_at=now)
                    await uow.consents.add(inn, consent)
                result.append(consent)

            if is_director:
                # Привязка представителя уже сделана при проверке доверенности, её не трогаем.
                link = user.company(company.inn)
                user.link_company(
                    UserCompany(
                        inn=company.inn,
                        name=company.name,
                        kind=company.kind,
                        is_director=True,
                        authority=link.authority if link else None,
                    )
                )
            await uow.commit()
        return result

    async def list_for(self, claims: TokenClaims, inn: str) -> list[Consent]:
        async with self._uow() as uow:
            return await uow.consents.list_for(inn, claims.user_id)


def _ensure_applicable(kind: CompanyKind, types: Sequence[ConsentType]) -> None:
    extra = [t for t in types if t not in _APPLICABLE[kind]]
    if extra:
        raise ValidationFailed(
            [
                FieldError(
                    "types",
                    "CONSENT_NOT_APPLICABLE",
                    f"Для этой формы бизнеса не нужны согласия: {', '.join(extra)}",
                )
            ]
        )
