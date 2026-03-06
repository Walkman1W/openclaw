"""TASK-010: Pydantic schemas for task publishing and retrieval."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskPublishRequest(BaseModel):
    title: str = Field(max_length=500)
    task_type: str = Field(max_length=50)
    # MVP: only Level 1-2 tasks are supported
    task_level: int = Field(ge=1, le=2)
    input_spec: dict[str, Any]
    output_spec: dict[str, Any]
    # Validated downstream by criteria_validator; kept as raw list here
    acceptance_criteria: list[dict[str, Any]]
    reward_pool: int = Field(ge=5)
    deadline: datetime
    required_tags: list[str] = Field(default_factory=list)
    preferred_tags: list[str] = Field(default_factory=list)
    min_reputation: int = Field(default=0, ge=0, le=2000)

    @field_validator("deadline")
    @classmethod
    def deadline_must_be_future(cls, v: datetime) -> datetime:
        now = datetime.now(tz=timezone.utc)
        # Normalise to UTC if the caller supplied a naive datetime
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v <= now:
            raise ValueError("deadline must be a future timestamp")
        return v

    @field_validator("required_tags", "preferred_tags")
    @classmethod
    def tags_not_empty_strings(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if not tag or len(tag) > 50:
                raise ValueError("each tag must be 1–50 characters")
        return tags


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    task_type: str
    task_level: int
    status: str
    reward_pool: int
    deadline: datetime
    required_tags: list[str]
    preferred_tags: list[str]
    min_reputation: int
    publisher_id: uuid.UUID
    content_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}
