"""AI endpoints (docs/features/11-ai-task-generator.md).

Draft endpoints are pure generation — zero persistence. Accept endpoints
treat the echoed proposal as untrusted input and run the full manual-path
business rules before writing.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMClient, build_llm_client
from app.ai.schemas import (
    AcceptBreakdownRequest,
    AcceptBreakdownResponse,
    AcceptProjectDraftRequest,
    AcceptProjectDraftResponse,
    AgentExecuteResponse,
    AgentProposalResponse,
    AgentToolCall,
    ChatResponse,
    ProjectProposalResponse,
    ProjectRiskResponse,
    ProjectSummaryResponse,
    TaskBreakdownResponse,
)
from app.api.deps import get_current_membership, get_db
from app.core.config import get_settings
from app.models import Membership, User
from app.schemas.validators import SafeStr
from app.services.permissions import is_manager_or_above

router = APIRouter(tags=["ai"])


def get_llm() -> LLMClient:
    """Composition point tests override with FakeLLMClient."""
    return build_llm_client()


def get_ai_service(session: AsyncSession = Depends(get_db)):
    """For ACCEPTANCE endpoints: proposals are plain data here — no LLM
    needed. A user must be able to persist an accepted draft even while the
    provider is unconfigured or down."""
    from app.ai.service import AiService

    return AiService(session, llm=None)


def get_generating_service(
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
):
    """For DRAFT endpoints: actually calls the provider (and therefore
    requires configuration)."""
    from app.ai.service import AiService

    return AiService(session, llm)


class ProjectDraftRequest(BaseModel):
    idea: SafeStr = Field(min_length=10, max_length=4000)
    title_hint: SafeStr | None = Field(default=None, max_length=255)


class BreakdownRequest(BaseModel):
    instruction: SafeStr = Field(min_length=5, max_length=2000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    question: SafeStr = Field(min_length=1, max_length=4000)
    # Server owns history after Phase 1; any client-supplied history is ignored.
    session_id: uuid.UUID | None = None
    history: list[ChatMessage] | None = None


@router.post("/ai/projects/drafts", response_model=ProjectProposalResponse)
async def draft_project(
    payload: ProjectDraftRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> ProjectProposalResponse:
    actor, membership = context
    if not is_manager_or_above(membership):
        from app.core.exceptions import ForbiddenError

        raise ForbiddenError()
    return await service.generate_project_draft(
        idea=payload.idea, title_hint=payload.title_hint
    )


@router.post(
    "/projects/from-drafts",
    response_model=AcceptProjectDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_project_draft(
    payload: AcceptProjectDraftRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_ai_service),
) -> AcceptProjectDraftResponse:
    actor, membership = context
    project, task_count, warnings = await service.accept_project_draft(
        actor=actor, actor_membership=membership, payload=payload
    )
    return AcceptProjectDraftResponse(
        project_id=project.id,
        project_name=project.name,
        created_task_count=task_count,
        warnings=warnings,
    )


@router.get(
    "/ai/projects/{project_id}/summary",
    response_model=ProjectSummaryResponse,
)
async def summarize_project(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> ProjectSummaryResponse:
    actor, membership = context
    return await service.generate_project_summary(
        actor=actor, actor_membership=membership, project_id=project_id
    )


@router.post("/ai/projects/{project_id}/chat", response_model=ChatResponse)
async def chat_project(
    project_id: uuid.UUID,
    payload: ChatRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> ChatResponse:
    actor, membership = context
    answer = await service.chat(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        question=payload.question,
        session_id=payload.session_id,
        history=[m.model_dump() for m in (payload.history or [])],
    )
    return ChatResponse(answer=answer, model=get_settings().llm_model)


@router.get(
    "/ai/projects/{project_id}/risks",
    response_model=ProjectRiskResponse,
)
async def assess_project_risk(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> ProjectRiskResponse:
    actor, membership = context
    return await service.risk_assessment(
        actor=actor, actor_membership=membership, project_id=project_id
    )


@router.post("/ai/tasks/{task_id}/breakdowns", response_model=TaskBreakdownResponse)
async def draft_breakdown(
    task_id: uuid.UUID,
    payload: BreakdownRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> TaskBreakdownResponse:
    actor, membership = context
    return await service.generate_task_breakdown(
        actor=actor,
        actor_membership=membership,
        task_id=task_id,
        instruction=payload.instruction,
    )


@router.post(
    "/tasks/{task_id}/accept-breakdowns",
    response_model=AcceptBreakdownResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_breakdown(
    task_id: uuid.UUID,
    payload: AcceptBreakdownRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_ai_service),
) -> AcceptBreakdownResponse:
    actor, membership = context
    created, warnings = await service.accept_task_breakdown(
        actor=actor, actor_membership=membership, task_id=task_id, payload=payload
    )
    return AcceptBreakdownResponse(
        parent_task_id=task_id, created=created, warnings=warnings
    )


# --- AI V5: agent (propose, then approve) ---


class AgentProposeRequest(BaseModel):
    instruction: SafeStr = Field(min_length=5, max_length=2000)


class AgentApproveRequest(BaseModel):
    actions: list[AgentToolCall] = Field(min_length=1, max_length=20)


@router.post(
    "/ai/projects/{project_id}/agent",
    response_model=AgentProposalResponse,
)
async def agent_propose(
    project_id: uuid.UUID,
    payload: AgentProposeRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_generating_service),
) -> AgentProposalResponse:
    """Plan actions only. Returns proposed SAFE tool calls for the caller to
    review. Nothing is executed until /agent/approve is called."""
    actor, membership = context
    return await service.propose_agent_actions(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        instruction=payload.instruction,
    )


@router.post(
    "/ai/projects/{project_id}/agent/approve",
    response_model=AgentExecuteResponse,
)
async def agent_approve(
    project_id: uuid.UUID,
    payload: AgentApproveRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service=Depends(get_ai_service),
) -> AgentExecuteResponse:
    """Execute an APPROVED list of actions. Each call is re-validated and run
    through its permission-enforcing service; the caller's role/tenancy still
    bound every write."""
    actor, membership = context
    return await service.execute_agent_actions(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        calls=payload.actions,
    )
