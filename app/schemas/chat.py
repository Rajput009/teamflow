"""Pydantic schemas for the short-term chat-memory API (docs/features/13 §3.5).

These are phase-1 request/response shapes. The `summary` field is intentionally
EXCLUDED from list responses (server-weight concern); clients get it by ID.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ChatMessage, ChatSession
from app.schemas.validators import SafeStr


class ChatSessionCreate(BaseModel):
    project_id: uuid.UUID


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    summary: str | None
    is_active: bool
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChatSessionListResponse(BaseModel):
    """List payload deliberately has NO `summary` — summary is detail-only
    (doc §3.2.1: title/timestamps/is_active only)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    is_active: bool
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    seq: int
    role: str
    content: str
    created_at: datetime


class ChatMessageCreate(BaseModel):
    content: SafeStr = Field(min_length=1, max_length=15000)


def session_to_response(session: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse.model_validate(session)


def session_list_response(session: ChatSession) -> ChatSessionListResponse:
    return ChatSessionListResponse.model_validate(session)


def message_to_response(message: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse.model_validate(message)
