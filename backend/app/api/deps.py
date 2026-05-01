"""Dependency injection and middleware utilities."""
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models import User
from app.services.auth import AuthService


async def get_current_user(
    access_token: Annotated[str | None, Cookie()] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> User:
    """Get current authenticated user from JWT cookie."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        auth_service = AuthService()
        payload = await auth_service.decode_token(access_token)
        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        user = await auth_service.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
