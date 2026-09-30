from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
class Strict(BaseModel): model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
class LoginIn(Strict):
    username: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_login_identifier(self):
        if not self.username and not self.email:
            raise ValueError("username or email is required")
        if self.username and self.email:
            raise ValueError("provide either username or email, not both")
        return self
class RefreshIn(Strict): refresh_token: str = Field(min_length=32)
class PasswordChangeIn(Strict): current_password: str = Field(min_length=1); new_password: str = Field(min_length=12, max_length=256)
class AccessScopeIn(Strict):
    scope_type: str = Field(pattern=r"^(PLATFORM|TENANT|FRANCHISE|BRANCH)$")
    scope_id: UUID | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope_type == "PLATFORM" and self.scope_id is not None: raise ValueError("platform scope must not have a scope_id")
        if self.scope_type != "PLATFORM" and self.scope_id is None: raise ValueError("scope_id is required")
        return self
class UserCreateIn(Strict):
    username: str = Field(min_length=3, max_length=128); password: str = Field(min_length=12, max_length=256)
    email: str | None = None; full_name: str | None = None; mobile: str | None = None; tenant_id: UUID | None = None; franchise_id: UUID | None = None; department_id: UUID | None = None
    roles: list[str] = ["READ_ONLY"]
    role_ids: list[UUID] = Field(default_factory=list)
    access_scopes: list[AccessScopeIn] = Field(default_factory=list)
    is_organization_owner: bool = False

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value is None: return None
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or "." not in normalized.rsplit("@", 1)[1]:
            raise ValueError("enter a valid email address")
        return normalized

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value):
        if value is None: return None
        digits = "".join(character for character in value if character.isdigit())
        if not 7 <= len(digits) <= 15:
            raise ValueError("mobile number must contain 7 to 15 digits")
        return value.strip()
class UserUpdateIn(Strict):
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = None
    mobile: str | None = None
    enabled: bool | None = None
    department_id: UUID | None = None
    role_ids: list[UUID] | None = None
    access_scopes: list[AccessScopeIn] | None = None
class AdminPasswordResetIn(Strict): new_password: str = Field(min_length=12, max_length=256)
class ServiceAccountCreateIn(Strict):
    name: str = Field(min_length=3, max_length=128)
    tenant_id: UUID | None = None
    permissions: list[str] = Field(min_length=1)

class DepartmentCreateIn(Strict):
    scope: str = Field(default="TENANT", pattern=r"^(PLATFORM|TENANT)$")
    tenant_id: UUID | None = None
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = True

class DepartmentUpdateIn(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    code: str | None = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None

class RoleCreateIn(Strict):
    scope: str = Field(default="TENANT", pattern=r"^(PLATFORM|TENANT)$")
    tenant_id: UUID | None = None
    department_id: UUID
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True

class RoleUpdateIn(Strict):
    department_id: UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=128)
    code: str | None = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] | None = None
    enabled: bool | None = None

class AccessTemplateCreateIn(Strict):
    scope: str = Field(pattern=r"^(PLATFORM|TENANT)$")
    tenant_id: UUID | None = None
    target_type: str = Field(pattern=r"^(TENANT|FRANCHISE)$")
    name: str = Field(min_length=2, max_length=128)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True

class AccessTemplateUpdateIn(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    code: str | None = Field(default=None, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] | None = None
    enabled: bool | None = None

class AccessTemplateAssignmentIn(Strict):
    template_id: UUID
