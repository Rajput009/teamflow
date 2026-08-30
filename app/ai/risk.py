"""AI V4 risk rules.

Every risk is *computed* from repository data — the LLM never invents one, and
never sets severity (severity is a deterministic function of thresholds here).
The LLM only narrates impact and recommends a mitigation per signal, over the
exact list this module returns.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.task_repository import TaskRepository


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class RiskSignal:
    kind: str
    severity: RiskSeverity
    evidence: dict


# Thresholds (kept as constants, not Settings — see WF-2 decision).
STALLED_DAYS = 5
OVERDUE_HIGH_DAYS = 7
WORKLOAD_RATIO = 2.0


async def assess_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    sees_all: bool,
) -> list[RiskSignal]:
    """Return the DB-computed risk signals for a project. Pure read; no LLM."""
    tasks = TaskRepository(session)
    signals: list[RiskSignal] = []

    now = datetime.now(UTC)
    today = now.date()

    overdue = await tasks.overdue_count(project_id, today)
    if overdue > 0:
        sev = RiskSeverity.HIGH if overdue >= OVERDUE_HIGH_DAYS else RiskSeverity.MEDIUM
        signals.append(RiskSignal("overdue_tasks", sev, {"overdue_count": overdue}))

    unassigned = await tasks.unassigned_high_urgent_count(project_id)
    if unassigned > 0:
        signals.append(
            RiskSignal("unassigned_high_urgent", RiskSeverity.HIGH, {"count": unassigned})
        )

    stale = await tasks.stale_open_count(project_id, cutoff=now - timedelta(days=STALLED_DAYS))
    if stale > 0:
        signals.append(
            RiskSignal(
                "stalled_open",
                RiskSeverity.MEDIUM,
                {"count": stale, "threshold_days": STALLED_DAYS},
            )
        )

    workload = await tasks.open_workload_top(project_id)
    owners = [w for w in workload if w["open_tasks"] > 0]
    if owners:
        counts = [w["open_tasks"] for w in owners]
        if len(owners) == 1:
            signals.append(
                RiskSignal(
                    "single_owner",
                    RiskSeverity.MEDIUM,
                    {"owner": owners[0]["email"], "open_tasks": owners[0]["open_tasks"]},
                )
            )
        elif max(counts) > WORKLOAD_RATIO * min(counts):
            signals.append(
                RiskSignal(
                    "unbalanced_workload",
                    RiskSeverity.HIGH,
                    {
                        "max": max(counts),
                        "min": min(counts),
                        "ratio": round(max(counts) / min(counts), 1),
                    },
                )
            )

    return signals
