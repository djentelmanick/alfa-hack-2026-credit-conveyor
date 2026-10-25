from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import Claims, get_auth_service
from app.api.v1.schemas.auth import LoginIn, LoginOut, RegisterIn, UserOut
from app.api.v1.schemas.common import AUTH_ERRORS
from app.api.v1.schemas.errors import ErrorOut
from app.application.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

Auth = Annotated[AuthService, Depends(get_auth_service)]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация клиента с проверкой паспорта",
    responses={409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
)
async def register(body: RegisterIn, auth: Auth) -> UserOut:
    return UserOut.from_domain(await auth.register(body.to_registration()))


@router.post(
    "/login",
    summary="Вход по логину и паролю",
    responses={**AUTH_ERRORS, 422: {"model": ErrorOut}},
)
async def login(body: LoginIn, auth: Auth) -> LoginOut:
    return LoginOut.from_domain(await auth.login(body.login, body.password))


@router.get("/me", summary="Текущий пользователь из токена", responses=AUTH_ERRORS)
async def me(claims: Claims, auth: Auth) -> UserOut:
    return UserOut.from_domain(await auth.me(claims))
