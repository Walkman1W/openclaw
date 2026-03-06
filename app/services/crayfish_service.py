"""TASK-021 / TASK-022: Crayfish Agent infrastructure.

Responsibilities:
  - Cold-start publisher: when pending task count < 10, auto-publish Level 1 tasks.
  - Onboarding: assign 5 verification tasks to newly registered agents.

Both run as Celery tasks (see workers/crayfish_tasks.py).
This module contains the async business logic; the Celery wrappers
call asyncio.run() around these coroutines.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.task import Task
from app.models.task_status_history import TaskStatusHistory
from app.services.bootstrap import get_crayfish_account_id
from app.services.claw_service import get_balance, lock_claw

# ---------------------------------------------------------------------------
# Verification task templates (TASK-022)
# ---------------------------------------------------------------------------

_VERIFICATION_TASKS = [
    {
        "title": "[Verification] Extract product names from HTML snippet",
        "task_type": "data_extraction",
        "task_level": 1,
        "input_spec": {
            "html": "<ul><li class='product'>Apple</li><li class='product'>Banana</li></ul>",
            "extract": "product names",
        },
        "output_spec": {"format": "json", "fields": ["products"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["products"]},
            {"type": "honeypot_exact_match", "field": "products"},
        ],
        "honeypot_answers": {"products": ["Apple", "Banana"]},
        "reward_pool": 0,
    },
    {
        "title": "[Verification] Convert CSV row to JSON",
        "task_type": "format_conversion",
        "task_level": 1,
        "input_spec": {"csv_row": "id,name,age\n1,Alice,30", "target_format": "json"},
        "output_spec": {"format": "json", "fields": ["id", "name", "age"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["id", "name", "age"]},
            {"type": "honeypot_exact_match", "field": "id"},
        ],
        "honeypot_answers": {"id": 1},
        "reward_pool": 0,
    },
    {
        "title": "[Verification] Count words in sentence",
        "task_type": "text_analysis",
        "task_level": 1,
        "input_spec": {"text": "The quick brown fox", "task": "count words"},
        "output_spec": {"format": "json", "fields": ["word_count"]},
        "acceptance_criteria": [
            {"type": "honeypot_exact_match", "field": "word_count"},
        ],
        "honeypot_answers": {"word_count": 4},
        "reward_pool": 0,
    },
    {
        "title": "[Verification] Extract numeric values from text",
        "task_type": "data_extraction",
        "task_level": 1,
        "input_spec": {"text": "Order #42 contains 3 items at $9.99 each."},
        "output_spec": {"format": "json", "fields": ["numbers"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["numbers"]},
            {"type": "honeypot_exact_match", "field": "numbers"},
        ],
        "honeypot_answers": {"numbers": [42, 3, 9.99]},
        "reward_pool": 0,
    },
    {
        "title": "[Verification] Classify sentiment as positive or negative",
        "task_type": "classification",
        "task_level": 1,
        "input_spec": {"text": "I love this product, it works great!"},
        "output_spec": {"format": "json", "fields": ["sentiment"]},
        "acceptance_criteria": [
            {"type": "honeypot_exact_match", "field": "sentiment"},
        ],
        "honeypot_answers": {"sentiment": "positive"},
        "reward_pool": 0,
    },
]

# Cold-start Level 1 task templates (TASK-021)
_COLD_START_TASKS = [
    {
        "title": "[ColdStart] Clean and deduplicate product list",
        "task_type": "data_cleaning",
        "task_level": 1,
        "input_spec": {
            "data": [
                {"id": 1, "name": "Widget A"},
                {"id": 2, "name": "Widget B"},
                {"id": 1, "name": "Widget A"},
            ],
            "task": "remove duplicate rows by id",
        },
        "output_spec": {"format": "json", "fields": ["data", "removed_count"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["data", "removed_count"]},
            {"type": "honeypot_exact_match", "field": "removed_count"},
        ],
        "honeypot_answers": {"removed_count": 1},
        "reward_pool": 10,
    },
    {
        "title": "[ColdStart] Convert address strings to structured JSON",
        "task_type": "format_conversion",
        "task_level": 1,
        "input_spec": {
            "addresses": ["123 Main St, Springfield, IL 62701"],
            "target_fields": ["street", "city", "state", "zip"],
        },
        "output_spec": {"format": "json", "fields": ["results"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["results"]},
            {"type": "row_count", "operator": ">=", "value": 1},
        ],
        "honeypot_answers": {},
        "reward_pool": 10,
    },
    {
        "title": "[ColdStart] Extract email addresses from text",
        "task_type": "data_extraction",
        "task_level": 1,
        "input_spec": {
            "text": "Contact us at support@example.com or sales@example.org for help.",
            "extract": "email addresses",
        },
        "output_spec": {"format": "json", "fields": ["emails"]},
        "acceptance_criteria": [
            {"type": "field_completeness", "required_fields": ["emails"]},
            {"type": "honeypot_exact_match", "field": "emails"},
        ],
        "honeypot_answers": {"emails": ["support@example.com", "sales@example.org"]},
        "reward_pool": 10,
    },
]

_COLDSTART_THRESHOLD = 10
_ONBOARDING_DEADLINE_HOURS = 72

# Benchmark tasks published daily (TASK-029)
_BENCHMARK_TASKS = [
    {
        "title": "[Benchmark] Extract city names from paragraph",
        "task_type": "data_extraction",
        "task_level": 1,
        "input_spec": {"text": "Travelers visited Paris, Tokyo, and New York last summer."},
        "output_spec": {"format": "json", "fields": ["cities"]},
        "acceptance_criteria": [
            {"type": "honeypot_exact_match", "field": "cities"},
        ],
        "honeypot_answers": {"cities": ["Paris", "Tokyo", "New York"]},
        "reward_pool": 5,
    },
    {
        "title": "[Benchmark] Convert temperature Celsius to Fahrenheit",
        "task_type": "computation",
        "task_level": 1,
        "input_spec": {"celsius": 100, "formula": "(C × 9/5) + 32"},
        "output_spec": {"format": "json", "fields": ["fahrenheit"]},
        "acceptance_criteria": [
            {"type": "honeypot_exact_match", "field": "fahrenheit"},
        ],
        "honeypot_answers": {"fahrenheit": 212},
        "reward_pool": 5,
    },
    {
        "title": "[Benchmark] Classify text language",
        "task_type": "classification",
        "task_level": 1,
        "input_spec": {"text": "Bonjour le monde"},
        "output_spec": {"format": "json", "fields": ["language"]},
        "acceptance_criteria": [
            {"type": "honeypot_exact_match", "field": "language"},
        ],
        "honeypot_answers": {"language": "French"},
        "reward_pool": 5,
    },
]


def _make_content_hash(template: dict, publisher_id: uuid.UUID) -> str:
    canonical = {k: v for k, v in template.items() if k != "honeypot_answers"}
    canonical["publisher_id"] = str(publisher_id)
    canonical["deadline_days"] = _ONBOARDING_DEADLINE_HOURS
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _publish_task_as_crayfish(
    db: AsyncSession,
    template: dict,
    crayfish_id: uuid.UUID,
) -> Task | None:
    """Publish one task on behalf of the crayfish account.

    Returns None if crayfish balance is insufficient (free tasks skip balance check).
    """
    reward_pool = template.get("reward_pool", 0)
    deadline = datetime.now(tz=timezone.utc) + timedelta(hours=_ONBOARDING_DEADLINE_HOURS)
    content_hash = _make_content_hash(template, crayfish_id)

    # Idempotency: don't re-publish the same template within 24 hours
    from datetime import timedelta as td
    cutoff = datetime.now(tz=timezone.utc) - td(hours=24)
    existing = await db.execute(
        select(Task).where(
            Task.publisher_id == crayfish_id,
            Task.content_hash == content_hash,
            Task.created_at >= cutoff,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    honeypot_answers = template.get("honeypot_answers", {})

    task = Task(
        publisher_id=crayfish_id,
        title=template["title"],
        task_type=template["task_type"],
        task_level=template["task_level"],
        input_spec=template["input_spec"],
        output_spec=template["output_spec"],
        acceptance_criteria=template["acceptance_criteria"],
        reward_pool=max(reward_pool, 5) if reward_pool > 0 else 5,
        deposit_held=max(reward_pool, 5) if reward_pool > 0 else 0,
        deadline=deadline,
        required_tags=[],
        min_reputation=0,
        status="pending",
        content_hash=content_hash,
        metadata_={"honeypot_answers": honeypot_answers} if honeypot_answers else {},
    )
    db.add(task)
    await db.flush()

    if reward_pool > 0:
        await lock_claw(
            db,
            account_id=crayfish_id,
            amount=max(reward_pool, 5),
            task_id=task.id,
            reason="crayfish_task_escrow",
        )

    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=None,
            to_status="pending",
            triggered_by=crayfish_id,
            note="published by crayfish agent",
        )
    )
    return task


# ---------------------------------------------------------------------------
# TASK-022: Onboarding – assign 5 verification tasks to new agent
# ---------------------------------------------------------------------------


async def assign_verification_tasks(db: AsyncSession, agent_id: uuid.UUID) -> int:
    """Publish and immediately assign 5 verification tasks to a new agent.

    Returns the number of tasks successfully assigned.
    """
    crayfish_id = await get_crayfish_account_id(db)

    # Verify agent exists and is unverified
    result = await db.execute(select(Account).where(Account.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None or agent.status != "unverified":
        return 0

    assigned = 0
    for template in _VERIFICATION_TASKS:
        task = await _publish_task_as_crayfish(db, template, crayfish_id)
        if task is None:
            continue  # idempotency skip

        # Immediately assign to the new agent (skip normal claim flow)
        task.status = "claimed"
        task.assignee_id = agent_id
        task.claim_expires_at = (
            datetime.now(tz=timezone.utc) + timedelta(hours=_ONBOARDING_DEADLINE_HOURS)
        )
        db.add(
            TaskStatusHistory(
                task_id=task.id,
                from_status="pending",
                to_status="claimed",
                triggered_by=crayfish_id,
                note=f"auto-assigned to unverified agent {agent_id}",
            )
        )
        assigned += 1

    await db.commit()
    return assigned


# ---------------------------------------------------------------------------
# TASK-021: Cold-start publisher
# ---------------------------------------------------------------------------


async def publish_benchmark_tasks(db: AsyncSession) -> int:
    """TASK-029: Publish 3 benchmark tasks with known answers daily."""
    crayfish_id = await get_crayfish_account_id(db)
    published = 0
    for template in _BENCHMARK_TASKS:
        task = await _publish_task_as_crayfish(db, template, crayfish_id)
        if task is not None:
            published += 1
    if published > 0:
        await db.commit()
    return published


async def maybe_publish_coldstart_tasks(db: AsyncSession) -> int:
    """Publish Level 1 tasks if the pending pool is below threshold.

    Returns the number of tasks published.
    """
    count_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == "pending")
    )
    pending_count = count_result.scalar() or 0

    if pending_count >= _COLDSTART_THRESHOLD:
        return 0

    crayfish_id = await get_crayfish_account_id(db)
    published = 0
    for template in _COLD_START_TASKS:
        task = await _publish_task_as_crayfish(db, template, crayfish_id)
        if task is not None:
            published += 1

    if published > 0:
        await db.commit()

    return published
