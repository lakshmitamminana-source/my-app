"""Authentication API routes."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models import User
from app.schemas import TokenResponse, UserLogin, UserRegister, UserResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _db_unavailable_error(action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "database_unreachable",
            "message": f"{action} is unavailable because the database cannot be reached. Reconnect VPN or restore IPv6 network access, then try again.",
        },
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegister,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> UserResponse:
    """Register a new user."""
    try:
        auth_service = AuthService()
        user = await auth_service.register_user(db, payload)
        return UserResponse.model_validate(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except (OSError, SQLAlchemyError):
        raise _db_unavailable_error("Registration")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "service_unavailable",
                "message": "Registration is temporarily unavailable. Please try again shortly.",
            },
        )


@router.post("/login", response_model=UserResponse)
async def login(
    payload: UserLogin,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> UserResponse:
    """Login user and set JWT in httpOnly cookie."""
    try:
        auth_service = AuthService()
        user = await auth_service.login_user(db, payload)

        # Create JWT token
        token = auth_service.create_access_token(user.id, user.email)

        # Set httpOnly cookie
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=480 * 60,  # 480 minutes
        )

        return UserResponse.model_validate(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except (OSError, SQLAlchemyError):
        raise _db_unavailable_error("Login")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "service_unavailable",
                "message": "Login is temporarily unavailable. Please try again shortly.",
            },
        )


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Logout user by clearing JWT cookie."""
    response.delete_cookie(key="access_token", samesite="lax")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> UserResponse:
    """Get current user info."""
    return UserResponse.model_validate(current_user)


@router.post("/google/login", response_model=UserResponse)
async def google_login(
    payload: dict,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> UserResponse:
    """Handle Google OAuth login/register using ID token from Google Sign-In.
    
    Request body: {"credential": "id_token_from_google"}
    """
    try:
        if not payload.get("credential"):
            raise ValueError("Google credential (ID token) is required")

        auth_service = AuthService()
        user = await auth_service.google_login_or_register(db, payload["credential"])

        # Create JWT token
        token = auth_service.create_access_token(user.id, user.email)

        # Set httpOnly cookie
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=480 * 60,  # 480 minutes
        )

        return UserResponse.model_validate(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except (OSError, SQLAlchemyError):
        raise _db_unavailable_error("Google login")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google OAuth error: {str(e)}",
        )
