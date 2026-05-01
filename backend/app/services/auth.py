"""Authentication service."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models import User
from app.schemas import UserLogin, UserRegister


class AuthService:
    """Service for authentication operations."""

    @staticmethod
    def validate_amzur_email(email: str) -> str:
        """Validate and normalize email, allowing only @amzur.com domain."""
        normalized_email = email.strip().lower()
        if not normalized_email.endswith("@amzur.com"):
            raise ValueError(
                "Only @amzur.com accounts are allowed. Please use your Amzur email."
            )
        return normalized_email

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password using bcrypt."""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode(), salt).decode()

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        """Verify password against hash."""
        return bcrypt.checkpw(password.encode(), hashed_password.encode())

    @staticmethod
    def create_access_token(user_id: uuid.UUID, email: str) -> str:
        """Create JWT access token."""
        payload = {
            "sub": str(user_id),
            "email": email,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

    @staticmethod
    async def decode_token(token: str) -> dict:
        """Decode and validate JWT token."""
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.InvalidTokenError:
            raise ValueError("Invalid token")

    async def register_user(self, db: AsyncSession, data: UserRegister) -> User:
        """Register a new user."""
        email = self.validate_amzur_email(data.email)

        # Check if user already exists
        stmt = select(User).where(User.email == email)
        existing = await db.execute(stmt)
        if existing.scalar_one_or_none():
            raise ValueError("User with this email already exists")

        # Create new user
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=self.hash_password(data.password),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def login_user(self, db: AsyncSession, data: UserLogin) -> User:
        """Authenticate user and return user object."""
        email = self.validate_amzur_email(data.email)
        stmt = select(User).where(User.email == email)
        user = await db.execute(stmt)
        user = user.scalar_one_or_none()

        if not user or not user.hashed_password:
            raise ValueError("Invalid email or password")

        if not self.verify_password(data.password, user.hashed_password):
            raise ValueError("Invalid email or password")

        return user

    async def get_user_by_id(self, db: AsyncSession, user_id: uuid.UUID) -> User | None:
        """Get user by ID."""
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        """Get user by email."""
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_google_id(self, db: AsyncSession, google_id: str) -> User | None:
        """Get user by Google ID."""
        stmt = select(User).where(User.google_id == google_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def exchange_google_code(self, code: str) -> dict:
        """Exchange Google authorization code for tokens and user info."""
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise ValueError("Google OAuth is not configured")

        token_url = "https://oauth2.googleapis.com/token"
        userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"

        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            token_response = await client.post(
                token_url,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )

            if token_response.status_code != 200:
                raise ValueError("Failed to exchange Google authorization code")

            tokens = token_response.json()
            access_token = tokens.get("access_token")

            # Get user info using access token
            userinfo_response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if userinfo_response.status_code != 200:
                raise ValueError("Failed to get Google user info")

            userinfo = userinfo_response.json()
            return {
                "google_id": userinfo.get("id"),
                "email": userinfo.get("email"),
                "name": userinfo.get("name"),
                "picture": userinfo.get("picture"),
            }

    async def verify_google_id_token(self, token: str) -> dict:
        """Verify Google ID token and extract user info.
        
        This method verifies the JWT token from Google Sign-In and extracts user claims.
        """
        if not settings.GOOGLE_CLIENT_ID:
            raise ValueError("Google OAuth is not configured")

        # Verify token with Google's public keys
        token_info_url = "https://oauth2.googleapis.com/tokeninfo"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_info_url,
                data={"id_token": token},
            )

            if response.status_code != 200:
                raise ValueError("Invalid Google ID token")

            token_info = response.json()

            # Verify the token is issued for our client ID
            if token_info.get("aud") != settings.GOOGLE_CLIENT_ID:
                raise ValueError("Token audience mismatch")

            email = self.validate_amzur_email(token_info.get("email", ""))

            return {
                "google_id": token_info.get("sub"),
                "email": email,
                "name": token_info.get("name"),
                "picture": token_info.get("picture"),
            }

    async def google_login_or_register(self, db: AsyncSession, code: str) -> User:
        """Handle Google OAuth login/register flow.
        
        Only @amzur.com accounts are permitted.
        If user exists (by google_id or email), return existing user.
        If user doesn't exist, create new user with Google profile.
        """
        # Verify Google ID token (also enforces @amzur.com domain)
        google_user = await self.verify_google_id_token(code)

        # Check if user exists by google_id
        user = await self.get_user_by_google_id(db, google_user["google_id"])
        if user:
            return user

        # Check if user exists by email (account linking)
        user = await self.get_user_by_email(db, google_user["email"])
        if user:
            # Link existing account with Google ID
            user.google_id = google_user["google_id"]
            await db.commit()
            await db.refresh(user)
            return user

        # Create new user with Google profile
        new_user = User(
            id=uuid.uuid4(),
            email=google_user["email"],
            google_id=google_user["google_id"],
            hashed_password=None,  # Google-only accounts have no password
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user
