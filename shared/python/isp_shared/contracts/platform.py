"""Public contract for platform identities and access-template ownership."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .http import JsonServiceClient, validate_response


class CreateOrganizationUserCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    email: str
    full_name: str
    mobile: str | None = None
    tenant_id: UUID
    franchise_id: UUID | None = None
    roles: list[str] = Field(default_factory=list)
    is_organization_owner: bool = False


class OrganizationUserCreated(BaseModel):
    id: UUID | str
    username: str


class AssignAccessTemplateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: UUID


class PlatformClient:
    def __init__(self, base_url: str, bearer_token: str):
        self.http = JsonServiceClient("Platform Core", base_url, {"Authorization": bearer_token})

    def create_organization_user(
        self,
        command: CreateOrganizationUserCommand,
        *,
        idempotency_key: str,
    ) -> OrganizationUserCreated:
        body = self.http.request(
            "POST",
            "/api/v1/platform/users",
            command.model_dump(mode="json"),
            timeout=10,
            headers={"Idempotency-Key": idempotency_key},
        )
        return validate_response("Platform Core", OrganizationUserCreated, body)

    def assign_access_template(self, owner_type: str, owner_id: UUID | str, template_id: UUID) -> None:
        command = AssignAccessTemplateCommand(template_id=template_id)
        self.http.request(
            "PUT",
            f"/api/v1/platform/access-template-assignments/{owner_type}/{owner_id}",
            command.model_dump(mode="json"),
            timeout=10,
        )
