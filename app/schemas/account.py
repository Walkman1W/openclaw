from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class HumanRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str


class HumanLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AgentRegisterRequest(BaseModel):
    name: str
    capability_tags: list[str] = Field(min_length=1, max_length=10)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AgentRegisterResponse(BaseModel):
    agent_id: uuid.UUID
    api_key: str
    initial_claw: int


class AccountStatusResponse(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str
    status: str
    rep_score: int
    capability_tags: list[str]

    model_config = {"from_attributes": True}
