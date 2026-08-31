"""AI orchestration: generate proposals, validate them hard, persist nothing.

The service sits between the probabilistic LLM and the deterministic domain:
upstream it retries structured-output failures; downstream it reuses
ProjectService/TaskService so accepted proposals pass through the exact same
business rules, permissions and audit trails as manual work.
"""
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from enum import Enum

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMClient
from app.ai.prompts import (
    PROMPT_AGENT_PROPOSE_V1,
    PROMPT_PROJECT_CHAT_V1,
    PROMPT_PROJECT_DRAFT_V1,
    PROMPT_PROJECT_RISK_V1,
    PROMPT_PROJECT_SUMMARY_V1,
    PROMPT_TASK_BREAKDOWN_V1,
    retry_suffix,
)
from app.ai.risk import assess_project
from app.ai.schemas import (
    AcceptBreakdownRequest,
    AcceptProjectDraftRequest,
    ActivitySample,
    AgentActionResult,
    AgentExecuteResponse,
    AgentProposalResponse,
    AgentToolCall,
    CreatedSubtaskResponse,
    ProjectProposal,
    ProjectProposalResponse,
    ProjectRiskResponse,
    ProjectStats,
    ProjectSummaryResponse,
    RiskItem,
    RiskNarrative,
    TaskBreakdown,
    TaskBreakdownResponse,
    WorkloadEntry,
)
from app.ai.tools import describe_tools, run_tool, validate_call
from app.core.config import get_settings
from app.core.exceptions import (
    AiInvalidOutputError,
    ForbiddenError,
    NotFoundError,
)
from app.models import ActionType, Membership, Task, User
from app.repositories.activity_repository import ActivityRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.task_repository import TaskRepository
from app.repositories.user_repository import UserRepository
from app.services.permissions import is_manager_or_above
from app.services.project_service import ProjectService
from app.services.task_service import TaskService


def _parse_json_object(raw: str) -> dict:
    """Extract a JSON object from model output. Tolerates markdown fences —
    models emit them constantly despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("reply contained no JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON value must be an object")
    return parsed


class AiService:
    def __init__(self, session: AsyncSession, llm: LLMClient) -> None:
        self._session = session
        self._llm = llm
        self._projects = ProjectRepository(session)
        self._tasks = TaskRepository(session)
        self._users = UserRepository(session)
        self._memberships = MembershipRepository(session)
        self._comments = CommentRepository(session)

    # --- drafting (never persists anything) ---

    async def generate_project_draft(
        self, *, idea: str, title_hint: str | None
    ) -> ProjectProposalResponse:
        self._require_llm()
        user_msg = idea.strip()
        if title_hint:
            user_msg = f"Suggested project name hint: {title_hint}\n\n{user_msg}"
        proposal = await self._validated_call(
            system=PROMPT_PROJECT_DRAFT_V1,
            user=user_msg,
            parse=self._to_proposal,
        )
        truncated = len(proposal.tasks) >= get_settings().ai_max_generated_tasks
        return ProjectProposalResponse(
            name=proposal.name,
            description=proposal.description,
            tasks=proposal.tasks,
            truncated_to_cap=truncated,
        )

    async def generate_task_breakdown(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        instruction: str,
    ) -> TaskBreakdownResponse:
        task = await self._visible_task(actor, actor_membership, task_id)
        self._require_llm()
        context = f"Task title: {task.title}"
        if task.description:
            context += f"\nTask description: {task.description[:1000]}"
        breakdown = await self._validated_call(
            system=PROMPT_TASK_BREAKDOWN_V1,
            user=f"{context}\n\nRequester instruction:\n{instruction.strip()}",
            parse=lambda data: TaskBreakdown.truncate_subtasks(
                data,
                get_settings().ai_max_generated_tasks,
            ),
        )
        return TaskBreakdownResponse(
            parent_task_id=task.id,
            parent_title=task.title,
            subtasks=breakdown.subtasks,
        )

    # --- acceptance (persists through the NORMAL service paths) ---

    async def accept_project_draft(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        payload: AcceptProjectDraftRequest,
    ) -> tuple[object, int, list[str]]:
        """Returns (project, created_task_count, warnings). The proposal is
        UNTRUSTED input: permissions and business rules are re-checked here,
        exactly as if the user had typed everything manually."""
        if not is_manager_or_above(actor_membership):
            raise ForbiddenError()

        warnings: list[str] = []
        cap = get_settings().ai_max_generated_tasks
        tasks = payload.tasks[:cap]
        if len(payload.tasks) > cap:
            warnings.append(f"More than {cap} tasks submitted — kept the first {cap}.")

        project = await self._projects_svc().create(
            actor=actor,
            actor_membership=actor_membership,
            name=payload.name,
            description=payload.description,
            deadline=None,
        )

        created_count = 0
        for gen in tasks:
            due_date = self._due_date(gen.due_in_days)
            assignee_id = await self._resolve_owner_email(
                gen.suggested_owner_email, actor_membership.organization_id, warnings
            )
            await self._tasks_svc().create(
                actor=actor,
                actor_membership=actor_membership,
                project_id=project.id,
                title=gen.title,
                description=gen.description,
                status=self._todo_status(),
                priority=gen.priority,
                due_date=due_date,
                assigned_to_id=assignee_id,
            )
            created_count += 1
            # V1 data model has no task hierarchy: proposed subtasks become
            # first-class sibling tasks inheriting the parent's scheduling.
            # A parent_task_id column is future work; until then "subtask"
            # is a presentation concept only at draft time.
            for sub_title in gen.subtasks:
                await self._tasks_svc().create(
                    actor=actor,
                    actor_membership=actor_membership,
                    project_id=project.id,
                    title=sub_title,
                    description=None,
                    status=self._todo_status(),
                    priority=gen.priority,
                    due_date=due_date,
                    assigned_to_id=assignee_id,
                )
                created_count += 1

        # The human approver is the actor — AI is recorded as the SOURCE.
        self._record_ai_activity(
            actor_membership=actor_membership,
            actor=actor,
            action=ActionType.AI_PROJECT_CREATED,
            entity_type="project",
            entity_id=project.id,
            new_value={"name": project.name, "task_count": created_count},
        )
        return project, created_count, warnings

    async def accept_task_breakdown(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        payload: AcceptBreakdownRequest,
    ) -> tuple[list[CreatedSubtaskResponse], list[str]]:
        parent = await self._visible_task(actor, actor_membership, task_id)
        warnings: list[str] = []
        cap = get_settings().ai_max_generated_tasks
        subtasks = payload.subtasks[:cap]

        created: list[CreatedSubtaskResponse] = []
        for gen in subtasks:
            assignee_id = await self._resolve_owner_email(
                gen.suggested_owner_email,
                actor_membership.organization_id,
                warnings,
            )
            task = await self._tasks_svc().create(
                actor=actor,
                actor_membership=actor_membership,
                project_id=parent.project_id,
                title=gen.title,
                description=gen.description,
                status=self._todo_status(),
                priority=gen.priority,
                due_date=self._due_date(gen.due_in_days),
                assigned_to_id=assignee_id,
            )
            created.append(CreatedSubtaskResponse.model_validate(task))
            # same flat-persistence rule as project drafts
            for sub_title in gen.subtasks:
                sibling = await self._tasks_svc().create(
                    actor=actor,
                    actor_membership=actor_membership,
                    project_id=parent.project_id,
                    title=sub_title,
                    description=None,
                    status=self._todo_status(),
                    priority=gen.priority,
                    due_date=self._due_date(gen.due_in_days),
                    assigned_to_id=assignee_id,
                )
                created.append(CreatedSubtaskResponse.model_validate(sibling))

        self._record_ai_activity(
            actor_membership=actor_membership,
            actor=actor,
            action=ActionType.AI_TASKS_GENERATED,
            entity_type="task",
            entity_id=parent.id,
            new_value={"count": len(created)},
        )
        return created, warnings

    # --- summarizing (AI V2: pure read, LLM narrates DB facts) ---

    async def generate_project_summary(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> ProjectSummaryResponse:
        self._require_llm()
        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        now = datetime.now(UTC)
        today = now.date()
        status_counts = await self._tasks.status_counts(project.id)
        total = sum(status_counts.values())
        completed = status_counts.get("COMPLETED", 0)

        workload = await self._tasks.open_workload_top(project.id)
        activity_rows, _ = await ActivityRepository(
            self._session
        ).list_for_project(project.id, page=1, limit=8)

        stats = ProjectStats(
            total_tasks=total,
            status_counts=status_counts,
            progress_pct=round(completed / total * 100) if total else 0,
            overdue_count=await self._tasks.overdue_count(project.id, today),
            unassigned_high_urgent_count=await (
                self._tasks.unassigned_high_urgent_count(project.id)
            ),
            due_within_week_count=await self._tasks.due_within_week_count(
                project.id, today
            ),
            stale_open_tasks_count=await self._tasks.stale_open_count(
                project.id, cutoff=now - timedelta(days=5)
            ),
            workload_top=[WorkloadEntry(**w) for w in workload],
            recent_activity=[
                ActivitySample(action=a.action, created_at=a.created_at)
                for a, _ in activity_rows
            ],
        )

        # The model receives ONLY these aggregates — never raw rows, and
        # nothing outside the caller's visibility.
        context = json.dumps(stats.model_dump(mode="json"), indent=1)
        summary = await self._narrative_call(
            system=PROMPT_PROJECT_SUMMARY_V1, user=context
        )
        return ProjectSummaryResponse(
            project_id=project.id,
            project_name=project.name,
            stats=stats,
            summary=summary,
        )

    # --- chat (AI V3: read-only, grounded Q&A) ---

    async def chat(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        question: str,
        session_id: uuid.UUID | None = None,
        history: list[dict] | None = None,  # ignored: server owns history
    ) -> str:
        self._require_llm()
        org_id = actor_membership.organization_id
        sees_all = is_manager_or_above(actor_membership)

        project = await self._projects.get_accessible(
            project_id, org_id, user_id=actor.id, sees_all=sees_all
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        from app.services.chat_memory_service import ChatMemoryService

        memory = ChatMemoryService(self._session)
        chat_session = await memory.get_or_create_session(
            actor=actor,
            membership=actor_membership,
            project_id=project_id,
            session_id=session_id,
        )
        await memory.append_user(
            actor=actor,
            membership=actor_membership,
            session_id=chat_session.id,
            content=question,
        )

        settings = get_settings()
        max_rows = settings.ai_context_block_max_rows
        tasks, _ = await self._tasks.list_for_project(
            project_id=project_id, limit=max_rows
        )
        members, _ = await self._memberships.list_members(org_id, limit=max_rows)
        activity_rows, _ = await ActivityRepository(self._session).list_for_project(
            project_id, page=1, limit=max_rows
        )
        comment_rows, _ = await self._comments.list_for_project(
            project_id=project_id,
            org_id=org_id,
            user_id=actor.id,
            sees_all=sees_all,
            limit=max_rows,
        )

        def _val(v):
            return v.value if isinstance(v, Enum) else v

        context = {
            "project": {
                "name": project.name,
                "description": project.description,
                "deadline": project.deadline.isoformat() if project.deadline else None,
            },
            "tasks": [
                {
                    "title": t.title,
                    "status": _val(t.status),
                    "priority": _val(t.priority),
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "assigned_to": str(t.assigned_to_id) if t.assigned_to_id else None,
                }
                for t in tasks
            ],
            "members": [{"email": u.email, "role": _val(m.role)} for m, u in members],
            "recent_activity": [
                {
                    "action": _val(a.action),
                    "actor": u.email,
                    "created_at": a.created_at.isoformat(),
                }
                for a, u in activity_rows
            ],
            "recent_comments": [
                {
                    "task_id": str(c.task_id),
                    "author": u.email,
                    "content": c.content,
                    "created_at": c.created_at.isoformat(),
                }
                for c, u in comment_rows
            ],
        }
        # Server-owned short-term memory: the DB tail + anchored summary are the
        # ONLY history; client-supplied history is intentionally ignored.
        context_block = json.dumps(context, indent=1, default=str)
        if len(context_block) > settings.ai_context_block_max_chars:
            context_block = context_block[: settings.ai_context_block_max_chars]
        user_message = await memory.assemble_prompt(
            session=chat_session,
            organization_id=org_id,
            user_id=actor.id,
            question=question,
            context_block=context_block,
        )
        answer = await self._narrative_call(
            system=PROMPT_PROJECT_CHAT_V1, user=user_message
        )
        await memory.append_assistant(
            membership=actor_membership,
            session_id=chat_session.id,
            content=answer,
        )
        return answer

    # --- risk detection (AI V4: DB-computed signals, LLM narrates only) ---

    async def risk_assessment(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
    ) -> ProjectRiskResponse:
        self._require_llm()
        org_id = actor_membership.organization_id
        sees_all = is_manager_or_above(actor_membership)
        project = await self._projects.get_accessible(
            project_id, org_id, user_id=actor.id, sees_all=sees_all
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        signals = await assess_project(
            self._session,
            project_id=project_id,
            org_id=org_id,
            user_id=actor.id,
            sees_all=sees_all,
        )

        # The model receives ONLY the computed signals — it narrates impact and
        # recommends mitigations, and NEVER invents or re-severs a risk.
        context = json.dumps(
            [
                {"kind": s.kind, "severity": s.severity.value, "evidence": s.evidence}
                for s in signals
            ],
            indent=1,
            default=str,
        )
        narration = await self._validated_call(
            system=PROMPT_PROJECT_RISK_V1,
            user=context,
            parse=RiskNarrative.model_validate,
        )
        recs = {r.kind: r.recommendation for r in narration.recommendations}
        risks = [
            RiskItem(
                kind=s.kind,
                severity=s.severity.value,
                evidence=s.evidence,
                recommendation=recs.get(s.kind, ""),
            )
            for s in signals
        ]
        return ProjectRiskResponse(
            project_id=project.id,
            project_name=project.name,
            risks=risks,
            narrative=narration.narrative,
        )

    async def _narrative_call(self, *, system: str, user: str) -> str:
        """Freeform-text call (no JSON contract): the only validation that
        makes sense here is non-emptiness, with one retry."""
        for attempt in range(2):
            message = user if attempt == 0 else user + retry_suffix(
                "the previous reply was empty"
            )
            text = (await self._llm.complete(system=system, user=message)).strip()
            if text:
                return text
        raise AiInvalidOutputError()

    # --- helpers ---

    def _require_llm(self) -> None:
        if self._llm is None:
            from app.core.exceptions import AiNotConfiguredError

            raise AiNotConfiguredError()

    def _projects_svc(self) -> ProjectService:
        return ProjectService(self._session)

    def _tasks_svc(self) -> TaskService:
        return TaskService(self._session)

    @staticmethod
    def _todo_status():
        from app.models import TaskStatus

        return TaskStatus.TODO

    @staticmethod
    def _due_date(due_in_days: int | None) -> date | None:
        if due_in_days is None:
            return None
        return datetime.now(UTC).date() + timedelta(days=due_in_days)

    async def _resolve_owner_email(
        self,
        email: str | None,
        org_id: uuid.UUID,
        warnings: list[str],
    ) -> uuid.UUID | None:
        """Map a suggested email to an org-member user id. Hallucinated or
        non-member addresses are DROPPED with a warning — never a failure."""
        if not email:
            return None
        user = await self._users.get_by_email(email.strip().lower())
        if user is None:
            warnings.append(f"No registered user with email '{email}' — owner suggestion ignored.")
            return None
        membership = await self._memberships.get_for_user(org_id, user.id)
        if membership is None:
            warnings.append(
                f"User '{email}' is not a member of this organization — owner suggestion ignored."
            )
            return None
        return user.id

    async def _visible_task(
        self, actor: User, actor_membership: Membership, task_id: uuid.UUID
    ) -> Task:
        task = await self._tasks.get_in_org(
            task_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if task is None:
            raise NotFoundError(message="Task not found.")
        return task

    def _record_ai_activity(
        self,
        *,
        actor_membership: Membership,
        actor: User,
        action: ActionType,
        entity_type: str,
        entity_id: uuid.UUID,
        new_value: dict,
    ) -> None:
        from app.repositories.activity_repository import ActivityRepository

        ActivityRepository(self._session).record(
            organization_id=actor_membership.organization_id,
            actor_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=None,
            new_value=new_value,
        )

    async def _validated_call(self, *, system: str, user: str, parse):
        """Call → parse → validate, with ONE validation-feedback retry.
        Models correct concrete error messages far more often than they
        spontaneously decide to emit valid JSON."""
        last_errors = "unknown"
        for attempt in range(2):
            message = user if attempt == 0 else user + retry_suffix(last_errors)
            raw = await self._llm.complete(system=system, user=message)
            try:
                data = _parse_json_object(raw)
                return parse(data)
            except ValueError as exc:
                last_errors = str(exc)
            except ValidationError as exc:
                last_errors = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in exc.errors()
                    [:5]
                )
        raise AiInvalidOutputError()

    def _to_proposal(self, data: dict) -> ProjectProposal:
        return ProjectProposal.truncate_tasks(data, get_settings().ai_max_generated_tasks)

    # --- AI V5: agent ---

    async def propose_agent_actions(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        instruction: str,
    ) -> AgentProposalResponse:
        """Turn a natural-language instruction into a plan of SAFE tool calls.
        Returns the plan; never executes anything. The caller must approve
        (a separate request) before any action is taken."""
        self._require_llm()

        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        user_msg = json.dumps(
            {"instruction": instruction, "allowed_tools": describe_tools()}
        )
        raw = await self._validated_call(
            system=PROMPT_AGENT_PROPOSE_V1, user=user_msg, parse=lambda d: d
        )

        calls: list[AgentToolCall] = []
        try:
            actions = raw["actions"]
            if not isinstance(actions, list):
                raise ValueError("actions must be a list")
            for item in actions:
                if not isinstance(item, dict):
                    raise ValueError("each action must be an object")
                tool = item.get("tool")
                args = item.get("args", {}) or {}
                validate_call(tool, args)
                calls.append(AgentToolCall(tool=tool, args=args))
        except (ValueError, TypeError, KeyError) as exc:
            # model returned a plan shape we cannot safely execute
            raise AiInvalidOutputError(
                message="Agent proposed an unsupported or invalid action."
            ) from exc
        return AgentProposalResponse(actions=calls)

    async def execute_agent_actions(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        calls: list[AgentToolCall],
    ) -> AgentExecuteResponse:
        """Execute an APPROVED list of tool calls. The approval step happens in
        a separate request; here we re-validate (defense in depth) and run each
        through its permission-enforcing service. The first failure aborts the
        plan, per the agent's 'no partial apply' contract."""
        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        results: list[AgentActionResult] = []
        for call in calls:
            try:
                validate_call(call.tool, call.args)
                result = await run_tool(
                    call.tool,
                    self._session,
                    actor,
                    actor_membership,
                    project_id,
                    call.args,
                )
            except Exception as exc:  # noqa: BLE001 - aborts the plan cleanly
                raise AiInvalidOutputError(
                    message=f"Tool '{call.tool}' failed: {exc}"
                ) from exc
            results.append(
                AgentActionResult(
                    tool=call.tool, args=call.args, ok=True, result=result
                )
            )
        return AgentExecuteResponse(results=results)
