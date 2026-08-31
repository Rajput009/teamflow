"""Short-term chat memory API (Phase 1 of docs/features/13-ai-memory.md).

Server owns history. The client never supplies history here; every write goes
through ChatMemoryService, which resolves the session by (org, user) and appends
under a row lock with a per-session seq. The summary is only ever produced by
the worker and is returned here for inspection — never memory.
"""
import uuid

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_db
from app.models import Membership, User
from app.schemas.chat import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    message_to_response,
    session_list_response,
    session_to_response,
)
from app.schemas.common import Page
from app.services.chat_memory_service import ChatMemoryService

router = APIRouter(prefix="/ai/projects", tags=["chat-sessions"])


def get_chat_memory_service(session: AsyncSession = Depends(get_db)) -> ChatMemoryService:
    return ChatMemoryService(session)


@router.post(
    "/{project_id}/chat/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
) -> ChatSessionResponse:
    actor, membership = context
    session = await service.create_session(
        actor=actor, membership=membership, project_id=project_id
    )
    return session_to_response(session)


@router.get("/{project_id}/chat/sessions", response_model=Page[ChatSessionListResponse])
async def list_chat_sessions(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> Page[ChatSessionResponse]:
    actor, membership = context
    items, total = await service.list_sessions(
        actor=actor,
        membership=membership,
        project_id=project_id,
        page=page,
        limit=limit,
    )
    return Page[ChatSessionListResponse].build(
        items=[session_list_response(s) for s in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.get(
    "/{project_id}/chat/sessions/{session_id}", response_model=ChatSessionResponse
)
async def get_chat_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
) -> ChatSessionResponse:
    actor, membership = context
    session = await service.get_session(
        actor=actor, membership=membership, session_id=session_id
    )
    return session_to_response(session)


class ChatSessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


@router.patch(
    "/{project_id}/chat/sessions/{session_id}", response_model=ChatSessionResponse
)
async def update_chat_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: ChatSessionPatchRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
) -> ChatSessionResponse:
    actor, membership = context
    fields = payload.model_dump(exclude_unset=True)
    session = await service.update_session(
        actor=actor,
        membership=membership,
        session_id=session_id,
        title=fields.get("title"),
        is_active=fields.get("is_active"),
    )
    return session_to_response(session)


@router.delete(
    "/{project_id}/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_chat_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
) -> None:
    actor, membership = context
    await service.delete_session(actor=actor, membership=membership, session_id=session_id)


@router.get(
    "/{project_id}/chat/sessions/{session_id}/messages",
    response_model=Page[ChatMessageResponse],
)
async def list_chat_messages(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> Page[ChatMessageResponse]:
    actor, membership = context
    items, total = await service.list_messages(
        actor=actor, membership=membership, session_id=session_id, page=page, limit=limit
    )
    return Page[ChatMessageResponse].build(
        items=[message_to_response(m) for m in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.post(
    "/{project_id}/chat/sessions/{session_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_chat_message(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: ChatMessageCreate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ChatMemoryService = Depends(get_chat_memory_service),
) -> ChatMessageResponse:
    actor, membership = context
    message = await service.append_user(
        actor=actor,
        membership=membership,
        session_id=session_id,
        content=payload.content,
    )
    return message_to_response(message)
