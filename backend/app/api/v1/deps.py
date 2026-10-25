from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.application.analyst_service import AnalystService
from app.application.application_service import ApplicationService
from app.application.auth_service import AuthService
from app.application.company_service import CompanyService
from app.application.consent_service import ConsentService
from app.application.contract_service import ContractService
from app.application.emulation_service import EmulationService
from app.application.expiration_job import ExpirationJob
from app.application.signing_service import SigningService
from app.domain.entities import TokenClaims
from app.domain.enums import Role, SourceType
from app.domain.errors import Forbidden, NotFound, TokenInvalid, TokenMissing
from app.domain.ports import TokenService
from app.domain.products import ProductCatalog


class HealthProbe(Protocol):
    async def check(self) -> bool: ...


class RealtimeHub(Protocol):
    """Подключения WebSocket. Реализация WsConnectionManager в app/infra/realtime."""

    async def connect(self, websocket: WebSocket, claims: TokenClaims) -> None: ...

    def disconnect(self, websocket: WebSocket) -> None: ...

    async def send(self, websocket: WebSocket, message: dict[str, object]) -> None: ...


class MockFailureSwitch(Protocol):
    """Режим «сломаться» у моков источников, только для демо."""

    def set(self, source: SourceType, inn: str, *, enabled: bool) -> None: ...


class Lifecycle(Protocol):
    """Фоновые задачи приложения: запускаются и останавливаются в lifespan."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Зависимости, которые main.py собирает на старте и кладёт в app.state."""

    db_probe: HealthProbe
    s3_probe: HealthProbe
    tokens: TokenService
    catalog: ProductCatalog
    auth: AuthService
    consents: ConsentService
    companies: CompanyService
    applications: ApplicationService
    analyst: AnalystService
    signing: SigningService
    contracts: ContractService
    emulation: EmulationService
    expiration: ExpirationJob
    realtime: RealtimeHub
    mock_failures: MockFailureSwitch
    lifecycle: Lifecycle
    demo_mode: bool = False


def get_container(connection: HTTPConnection) -> Container:
    # HTTPConnection общий предок Request и WebSocket: контейнер нужен обоим.
    container: Container = connection.app.state.container
    return container


def get_db_probe(request: Request) -> HealthProbe:
    return get_container(request).db_probe


def get_s3_probe(request: Request) -> HealthProbe:
    return get_container(request).s3_probe


def get_catalog(request: Request) -> ProductCatalog:
    return get_container(request).catalog


def get_auth_service(request: Request) -> AuthService:
    return get_container(request).auth


def get_consent_service(request: Request) -> ConsentService:
    return get_container(request).consents


def get_company_service(request: Request) -> CompanyService:
    return get_container(request).companies


def get_application_service(request: Request) -> ApplicationService:
    return get_container(request).applications


def get_analyst_service(request: Request) -> AnalystService:
    return get_container(request).analyst


def get_signing_service(request: Request) -> SigningService:
    return get_container(request).signing


def get_contract_service(request: Request) -> ContractService:
    return get_container(request).contracts


def get_demo_container(request: Request) -> Container:
    container = get_container(request)
    if not container.demo_mode:
        # Вне демо эндпоинта для клиента нет вовсе, поэтому 404, а не 403.
        raise NotFound()
    return container


def get_emulation_service(
    container: Annotated[Container, Depends(get_demo_container)],
) -> EmulationService:
    return container.emulation


# Только для схемы OpenAPI (кнопка Authorize в /docs). С auto_error=False HTTPBearer отдаёт None
# и без заголовка, и при чужой схеме, а нам нужно различать TOKEN_MISSING и TOKEN_INVALID.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT из /auth/login")

type RoleDependency = Callable[[TokenClaims], Awaitable[TokenClaims]]


class CurrentUser:
    async def __call__(
        self,
        request: Request,
        _: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    ) -> TokenClaims:
        header = request.headers.get("Authorization")
        if header is None or not header.strip():
            raise TokenMissing()
        scheme, _sep, token = header.strip().partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise TokenInvalid()
        return get_container(request).tokens.decode(token)

    def require_role(self, *roles: Role) -> RoleDependency:
        allowed = frozenset(roles)

        async def dependency(claims: Annotated[TokenClaims, Depends(self)]) -> TokenClaims:
            if claims.role not in allowed:
                raise Forbidden()
            return claims

        return dependency


current_user = CurrentUser()
require_role = current_user.require_role

# Обычные алиасы, а не `type X = ...`: FastAPI разбирает Annotated, но не TypeAliasType.
Claims = Annotated[TokenClaims, Depends(current_user)]
ClientClaims = Annotated[TokenClaims, Depends(require_role(Role.CLIENT))]
AnalystClaims = Annotated[TokenClaims, Depends(require_role(Role.ANALYST))]
