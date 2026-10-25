from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class User(Base):
    """An authenticated account.

    The password is stored only as a PBKDF2 hash with a per-user salt. Nothing in this project ever
    stores a credential for a third-party service, per architecture section 47.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def subject(self) -> str:
        """The value used as `user_id` everywhere collections are scoped."""
        return f"user:{self.id}"
