from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import ApplicationStatus, PipelineStep
from app.domain.state_machine import FINAL_STATUSES


@dataclass(frozen=True, kw_only=True, slots=True)
class StatusChangedEvent:
    application_id: UUID
    applicant_id: UUID
    from_status: ApplicationStatus
    to_status: ApplicationStatus
    reason: str
    at: datetime
    # Срок действия одобрения или контрпредложения: фронт показывает обратный отсчёт.
    expires_at: datetime | None = None

    @property
    def is_final(self) -> bool:
        return self.to_status in FINAL_STATUSES


@dataclass(frozen=True, kw_only=True, slots=True)
class PipelineStepEvent:
    """Какой источник конвейер сейчас проверяет. Статус заявки не меняет."""

    application_id: UUID
    applicant_id: UUID
    step: PipelineStep
    label: str
    at: datetime


type ApplicationEvent = StatusChangedEvent | PipelineStepEvent
