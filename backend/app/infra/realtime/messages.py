"""Сообщения сервера по WebSocket (docs/API.md, раздел 4.7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.domain.entities import Application
from app.domain.enums import ApplicationStatus, PipelineStep
from app.domain.events import ApplicationEvent, StatusChangedEvent


class StatusChangedMessage(BaseModel):
    type: Literal["application.status_changed"] = "application.status_changed"
    application_id: UUID
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    reason: str | None = None
    is_final: bool
    expires_at: datetime | None = None
    at: datetime


class StepMessage(BaseModel):
    type: Literal["application.step"] = "application.step"
    application_id: UUID
    step: PipelineStep
    label: str
    at: datetime


class SnapshotItem(BaseModel):
    application_id: UUID
    status: ApplicationStatus
    updated_at: datetime


class SnapshotMessage(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    applications: list[SnapshotItem]


def event_message(event: ApplicationEvent) -> dict[str, object]:
    message: BaseModel
    if isinstance(event, StatusChangedEvent):
        message = StatusChangedMessage(
            application_id=event.application_id,
            from_status=event.from_status,
            to_status=event.to_status,
            reason=event.reason or None,
            is_final=event.is_final,
            expires_at=event.expires_at,
            at=event.at,
        )
    else:
        message = StepMessage(
            application_id=event.application_id, step=event.step, label=event.label, at=event.at
        )
    return message.model_dump(mode="json", exclude_none=True)


def snapshot_message(applications: list[Application]) -> dict[str, object]:
    return SnapshotMessage(
        applications=[
            SnapshotItem(application_id=a.id, status=a.status, updated_at=a.updated_at)
            for a in applications
        ]
    ).model_dump(mode="json")
