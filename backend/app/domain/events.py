from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import ApplicationStatus
from app.domain.state_machine import FINAL_STATUSES


@dataclass(frozen=True, kw_only=True, slots=True)
class StatusChangedEvent:
    application_id: UUID
    applicant_id: UUID
    from_status: ApplicationStatus
    to_status: ApplicationStatus
    reason: str
    at: datetime

    @property
    def is_final(self) -> bool:
        return self.to_status in FINAL_STATUSES
