from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, kw_only=True, slots=True)
class AuditEntry:
    id: UUID
    actor_id: UUID | None
    action: str
    target_id: UUID
    details: Mapping[str, str] = field(default_factory=dict)
    at: datetime
