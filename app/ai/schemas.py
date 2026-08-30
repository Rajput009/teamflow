"""Pydantic contracts for LLM output.

These models are the boundary between probabilistic text and typed code:
whatever the model returns must parse into them or the response is rejected
(and fed back once for a retry). They double as the API response shape —
the proposal shown to the human is exactly what was validated.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import TaskPriority
from app.schemas.validators import NotBlankStr, SafeStr


class GeneratedTask(BaseModel):
    """One task inside an AI proposal. `due_in_days` stays RELATIVE on
    purpose: the model shouldn't do calendar math; acceptance converts it to
    a concrete date."""

    title: NotBlankStr = Field(min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=2000)
    priority: TaskPriority = TaskPriority.MEDIUM
    due_in_days: int | None = Field(default=None, ge=0, le=365)
    subtasks: list[NotBlankStr] = Field(default_factory=list, max_length=10)
    suggested_owner_email: str | None = Field(default=None, max_length=320)


class ProjectProposal(BaseModel):
    name: NotBlankStr = Field(min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=5000)
    tasks: list[GeneratedTask] = Field(min_length=1)

    @classmethod
    def truncate_tasks(cls, data: dict, cap: int) -> "ProjectProposal":
        """Enforce ai_max_generated_tasks. Applied at the service layer —
        truncation policy is business logic, not schema validation."""
        if len(data.get("tasks", [])) > cap:
            data["tasks"] = data["tasks"][:cap]
            data["truncated"] = True
        return cls.model_validate(data)


class ProjectProposalResponse(BaseModel):
    """What the draft endpoint returns: the validated proposal plus metadata
    about how it was produced."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str | None
    tasks: list[GeneratedTask]
    truncated_to_cap: bool = False


class AcceptProjectDraftRequest(BaseModel):
    """The client echoes back (possibly edited) what the draft returned.
    Treated as fully untrusted: every rule re-runs on acceptance."""

    name: NotBlankStr = Field(min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=5000)
    tasks: list[GeneratedTask] = Field(min_length=1)


class CreatedSubtaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    priority: TaskPriority
    due_date: date | None
    assigned_to_id: uuid.UUID | None


class TaskBreakdown(BaseModel):
    subtasks: list[GeneratedTask] = Field(min_length=1)

    @classmethod
    def truncate_subtasks(cls, data: dict, cap: int) -> "TaskBreakdown":
        if len(data.get("subtasks", [])) > cap:
            data["subtasks"] = data["subtasks"][:cap]
            data["truncated"] = True
        return cls.model_validate(data)


class TaskBreakdownResponse(BaseModel):
    parent_task_id: uuid.UUID
    parent_title: str
    subtasks: list[GeneratedTask]
    truncated_to_cap: bool = False


class AcceptBreakdownRequest(BaseModel):
    subtasks: list[GeneratedTask] = Field(min_length=1)


class AcceptBreakdownResponse(BaseModel):
    parent_task_id: uuid.UUID
    created: list[CreatedSubtaskResponse]
    warnings: list[str]


class AcceptProjectDraftResponse(BaseModel):
    project_id: uuid.UUID
    project_name: str
    created_task_count: int
    warnings: list[str]


# --- AI V2: summarizer ---


class WorkloadEntry(BaseModel):
    email: str
    open_tasks: int


class ActivitySample(BaseModel):
    action: str
    created_at: datetime


class ProjectStats(BaseModel):
    """Ground truth computed by PostgreSQL. The LLM narrates these numbers;
    it is never their source."""

    total_tasks: int
    status_counts: dict[str, int]
    progress_pct: int
    overdue_count: int
    unassigned_high_urgent_count: int
    due_within_week_count: int
    stale_open_tasks_count: int
    workload_top: list[WorkloadEntry]
    recent_activity: list[ActivitySample]


class ProjectSummaryResponse(BaseModel):
    project_id: uuid.UUID
    project_name: str
    stats: ProjectStats
    summary: str


# --- AI V3: project chat ---


class ChatResponse(BaseModel):
    """The model's grounded reply plus the model name that produced it."""

    answer: str
    model: str


# --- AI V4: risk detection ---


class RiskItem(BaseModel):
    """One computed risk signal plus the LLM's recommendation for it."""

    kind: str
    severity: str
    evidence: dict
    recommendation: str


class ProjectRiskResponse(BaseModel):
    project_id: uuid.UUID
    project_name: str
    risks: list[RiskItem]
    narrative: str


class RiskRec(BaseModel):
    kind: str
    recommendation: str


class RiskNarrative(BaseModel):
    """Structured LLM output for risk narration (validated, not trusted raw)."""

    narrative: str
    recommendations: list[RiskRec]


# --- AI V5: agent ---


class AgentToolCall(BaseModel):
    """One proposed/approved action. `args` is validated per-tool on execution;
    it arrives here as untrusted input (never executed blindly)."""

    tool: str
    args: dict = Field(default_factory=dict)


class AgentProposalResponse(BaseModel):
    actions: list[AgentToolCall]


class AgentActionResult(BaseModel):
    tool: str
    args: dict
    ok: bool
    result: dict | None = None
    error: str | None = None


class AgentExecuteResponse(BaseModel):
    results: list[AgentActionResult]
