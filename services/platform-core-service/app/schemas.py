from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
class Strict(BaseModel): model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
class LoginIn(Strict): username: str = Field(min_length=1, max_length=128); password: str = Field(min_length=1, max_length=256)
class RefreshIn(Strict): refresh_token: str = Field(min_length=32)
class PasswordChangeIn(Strict): current_password: str = Field(min_length=1); new_password: str = Field(min_length=12, max_length=256)
class UserCreateIn(Strict):
    username: str = Field(min_length=3, max_length=128); password: str = Field(min_length=12, max_length=256)
    email: str | None = None; full_name: str | None = None; tenant_id: UUID | None = None; roles: list[str] = ["READ_ONLY"]
class AdminPasswordResetIn(Strict): new_password: str = Field(min_length=12, max_length=256)
class ServiceAccountCreateIn(Strict):
    name: str = Field(min_length=3, max_length=128)
    tenant_id: UUID | None = None
    permissions: list[str] = Field(min_length=1)
