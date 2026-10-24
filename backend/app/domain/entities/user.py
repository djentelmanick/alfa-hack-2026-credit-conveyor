from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import Role


@dataclass(kw_only=True, eq=False)
class User:
    id: UUID
    login: str
    password_hash: str
    role: Role
    full_name: str
    passport_verified: bool
    created_at: datetime
