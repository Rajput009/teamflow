import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models import OrgRole
from app.schemas.validators import NotBlankStr, SafeStr


class OrganizationCreate(BaseModel):
    name: NotBlankStr = Field(min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=2000)


class OrganizationUpdate(BaseModel):
    """PATCH semantics: absent = untouched, null = clear. description is the
    only nullable column; an explicit null name is rejected at the gate."""

    name: NotBlankStr | None = Field(default=None, min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _forbid_clearing_name(self) -> "OrganizationUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("'name' cannot be cleared")
        return self


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    created_by_id: uuid.UUID
    created_at: datetime


class MemberAdd(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class MemberRoleUpdate(BaseModel):
    role: OrgRole


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: OrgRole


class MyMembershipResponse(BaseModel):
    organization: OrganizationResponse
    my_role: OrgRole
