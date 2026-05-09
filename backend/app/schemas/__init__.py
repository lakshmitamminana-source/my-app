"""Pydantic schemas for API requests and responses."""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    """User registration request."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=255)


class UserLogin(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response."""

    id: uuid.UUID
    email: str
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class MessageTurn(BaseModel):
    """Single message in a chat turn."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatAttachment(BaseModel):
    """Attachment payload for rich chat inputs."""

    type: Literal["image", "video", "table", "formula", "code", "pdf"]
    name: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=255)
    text_content: Optional[str] = Field(default=None)
    data_url: Optional[str] = Field(default=None, max_length=6000000)
    language: Optional[str] = Field(default=None, max_length=50)


class MessageResponse(BaseModel):
    """Message response."""

    id: uuid.UUID
    thread_id: uuid.UUID
    role: str
    content: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


class ThreadResponse(BaseModel):
    """Thread response with messages."""

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]

    class Config:
        from_attributes = True


class ThreadListResponse(BaseModel):
    """Thread list response (without messages)."""

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    """Chat message request."""

    message: str = Field(min_length=1, max_length=8000)
    history: list[MessageTurn] = Field(default_factory=list)
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Chat response."""

    answer: str
    assistant_attachments: list[ChatAttachment] = Field(default_factory=list)
    message_id: Optional[uuid.UUID] = None
    thread_title: Optional[str] = None
