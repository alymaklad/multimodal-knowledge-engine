import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately permissive: the only reliable proof an address exists is delivering mail to it, so
# this rejects obvious mistakes rather than pretending to validate deliverability.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

MINIMUM_PASSWORD_LENGTH = 12


class _EmailField(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.match(value):
            raise ValueError("Enter a valid email address")
        return value


class RegisterRequest(_EmailField):
    password: str = Field(min_length=MINIMUM_PASSWORD_LENGTH, max_length=200)


class LoginRequest(_EmailField):
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str


class UserRead(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
