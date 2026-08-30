from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models import User
from app.schemas.auth import LoginRequest, RegisterResponse, TokenPair
from app.schemas.user import UserCreate, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(session)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    auth: AuthService = Depends(get_auth_service),
) -> RegisterResponse:
    user, access_token, refresh_token = await auth.register(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )
    return RegisterResponse(
        user=UserResponse.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    auth: AuthService = Depends(get_auth_service),
) -> TokenPair:
    _, access_token, refresh_token = await auth.login(
        email=payload.email, password=payload.password
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),
) -> TokenPair:
    _, access_token, new_refresh = await auth.refresh(raw_refresh_token=payload.refresh_token)
    return TokenPair(access_token=access_token, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.logout(raw_refresh_token=payload.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Profile of the authenticated caller — our first protected endpoint."""
    return UserResponse.model_validate(current_user)
