import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.validators import NotBlankStr


class UserCreate(BaseModel):
    """Registration input. Validation rules come straight from the feature doc:
    email format, password >= 8 chars, name 1-255."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: NotBlankStr = Field(min_length=1, max_length=255)


class UserResponse(BaseModel):
    """API contract for a user — note there is NO password field.
    The model and this schema are deliberately different types."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime
