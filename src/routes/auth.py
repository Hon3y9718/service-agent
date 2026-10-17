import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.params import Cookie
from src.models.auth.refresh_token import RefreshToken
from src.config import settings
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from src.db import get_session
from src.deps import get_current_user
from src.models import User
from src.schemas.users import LoginRequest, LoginResponse, LoginResponse, Token, UserCreate, UserRead, RefreshRequest
from src.security import (
    SALTED_HASH,
    create_access_token,
    hash_password,
    verify_password,
    generate_refresh_token,
    hash_token,
)

COOKIE_NAME = "refresh_token"
COOKIE_PATH = "/api/auth"

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: AsyncSession = Depends(get_session)):
    email = data.email.lower()
    first_name = data.first_name.strip()
    last_name = data.last_name.strip()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(email=email, first_name=first_name, last_name=last_name, hashed_password=hash_password(data.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    await session.refresh(user)
    return user

@router.post("/login", response_model=LoginResponse)
async def login(
    response: Response,
    data: LoginRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await session.scalar(select(User).where(User.email == data.email.lower()))
    """
    If the email doesn't exist and you return immediately, that request finishes in a 
    couple of milliseconds. If the email does exist, you run argon2 verification, which 
    is deliberately slow (tens to hundreds of milliseconds). An attacker can measure 
    response times and tell which emails are registered, even though both cases return 
    the same “Incorrect email or password” message.

    By running verify_password against a dummy hash when the user is missing, both paths do 
    the same expensive work, so response times look alike. The result of that call is thrown 
    away. ok = False is what decides the outcome, and the dummy verify exists purely to burn 
    the same amount of time.
    """
    if user is None:
        verify_password(data.password, SALTED_HASH)  # Prevent timing attacks
        ok = False
    else:
        ok = verify_password(data.password, user.hashed_password)

    if not ok or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    
    refresh = await issue_refresh_token(session, user.id)
    await session.commit()
    set_refresh_cookie(response, refresh)

    return LoginResponse(access_token=create_access_token(str(user.id)), refresh_token=refresh, user=user)

def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path=COOKIE_PATH,
    )

async def issue_refresh_token(
    session: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> str:
    raw = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id or uuid.uuid4(),
            token_hash=hash_token(raw),
            expires_at=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return raw

async def rotate_refresh_token(
    session: AsyncSession, raw_token: str
) -> tuple[User, str]:
    """Validate a refresh token, revoke it, and return (user, new_raw_token)."""
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    db_token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if db_token is None:
        raise invalid

    now = datetime.datetime.now(datetime.timezone.utc)

    if db_token.revoked_at is not None:
        # Reuse of a rotated token: revoke the whole family
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == db_token.family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.commit()
        raise invalid

    if db_token.expires_at < now:
        raise invalid

    user = await session.get(User, db_token.user_id)
    if user is None or not user.is_active:
        raise invalid

    db_token.revoked_at = now
    new_raw = await issue_refresh_token(session, user.id, db_token.family_id)
    await session.commit()
    return user, new_raw

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    await session.commit()
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/refresh", response_model=Token, response_model_exclude_none=True)
async def refresh(
    response: Response,
    body: RefreshRequest | None = None,
    cookie_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
):
    raw = body.refresh_token if body else cookie_token
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")

    user, new_raw = await rotate_refresh_token(session, raw)
    access = create_access_token(str(user.id))

    set_refresh_cookie(response, new_raw)  # came from the cookie: reply with cookie
    return Token(access_token=access, refresh_token=new_raw)

@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.datetime.now(datetime.timezone.utc))
    )
    await session.commit()
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)

@router.post("/logout-others", status_code=status.HTTP_204_NO_CONTENT)
async def logout_others(
    body: RefreshRequest | None = None,
    cookie_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    raw = body.refresh_token if body else cookie_token
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current refresh token required")

    current = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(raw),
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    if current is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.family_id != current.family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.datetime.now(datetime.timezone.utc))
    )
    await session.commit()

@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)):
    return user