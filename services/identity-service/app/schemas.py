from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UserCreateIn(StrictModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    role: str = Field(default="READ_ONLY", max_length=64)
    tenant_id: UUID | None = None


class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class RegisterIn(StrictModel):
    """Self-service signup for the frontend.

    The role is assigned server-side from IDENTITY_REGISTRATION_ROLE (default
    READ_ONLY); public callers can never escalate their own role.
    """
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
