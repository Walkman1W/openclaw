"""TASK-011 / TASK-012: Task matching and recommendation.

Matching score formula (from TASKS.md):
  0.40 * capability_coverage
  + 0.25 * rep_normalized
  + 0.20 * completion_rate
  + 0.10 * response_speed
  + 0.05 * random_jitter
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task

if TYPE_CHECKING:
    from app.models.account import Account

_REP_MAX = 2000  # ceiling for normalization
_RECOMMEND_POOL = 200  # max candidates fetched from DB before in-Python sorting


def compute_match_score(agent: "Account", task: Task) -> float:
    """Return a match score in [0, 1] for the given agent/task pair."""
    required = set(task.required_tags)
    agent_tags = set(agent.capability_tags)

    # Fraction of required tags the agent covers
    capability_coverage = (len(required & agent_tags) / len(required)) if required else 1.0

    # Reputation normalized to [0, 1]
    rep_normalized = min(agent.rep_score / _REP_MAX, 1.0)

    # Historical completion rate and response speed from agent metadata
    meta = agent.metadata_ or {}
    completion_rate: float = float(meta.get("completion_rate", 0.5))
    response_speed: float = float(meta.get("response_speed", 0.5))

    jitter = random.random()

    return (
        0.40 * capability_coverage
        + 0.25 * rep_normalized
        + 0.20 * completion_rate
        + 0.10 * response_speed
        + 0.05 * jitter
    )


async def get_recommended_tasks(
    db: AsyncSession,
    agent: "Account",
    limit: int = 20,
) -> list[tuple[Task, float]]:
    """Return (task, score) pairs sorted by match score descending.

    Fetches up to _RECOMMEND_POOL candidates from DB (status=pending,
    min_reputation met), then filters by tag containment and ranks in Python.
    """
    result = await db.execute(
        select(Task)
        .where(
            Task.status == "pending",
            Task.min_reputation <= agent.rep_score,
        )
        .limit(_RECOMMEND_POOL)
    )
    candidates = result.scalars().all()

    agent_tags = set(agent.capability_tags)
    scored: list[tuple[Task, float]] = []
    for task in candidates:
        # Agent must cover ALL required tags
        if task.required_tags and not set(task.required_tags).issubset(agent_tags):
            continue
        score = compute_match_score(agent, task)
        scored.append((task, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
