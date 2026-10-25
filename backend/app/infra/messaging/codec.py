"""JSON задач конвейера и событий заявок.

Запись и чтение рядом, чтобы формат менялся в одном месте.
"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.enums import ApplicationStatus, PipelineStep
from app.domain.events import ApplicationEvent, PipelineStepEvent, StatusChangedEvent
from app.domain.ports import PipelineTask

CONTENT_TYPE = "application/json"
STATUS_CHANGED = "status_changed"
STEP = "step"


class MalformedMessageError(ValueError):
    """Тело сообщения нельзя разобрать: повтор не поможет, место ему в DLQ."""


def encode_task(task: PipelineTask) -> bytes:
    return _dump({"application_id": str(task.application_id), "run_id": str(task.run_id)})


def decode_task(body: bytes) -> PipelineTask:
    data = _load(body)
    try:
        return PipelineTask(
            application_id=UUID(data["application_id"]), run_id=UUID(data["run_id"])
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MalformedMessageError(f"Задача конвейера без нужных полей: {exc!r}") from exc


def encode_event(event: ApplicationEvent) -> bytes:
    common = {
        "application_id": str(event.application_id),
        "applicant_id": str(event.applicant_id),
        "at": event.at.isoformat(),
    }
    match event:
        case StatusChangedEvent():
            return _dump(
                {
                    **common,
                    "type": STATUS_CHANGED,
                    "from_status": event.from_status.value,
                    "to_status": event.to_status.value,
                    "reason": event.reason,
                    "expires_at": event.expires_at.isoformat() if event.expires_at else None,
                }
            )
        case PipelineStepEvent():
            return _dump({**common, "type": STEP, "step": event.step.value, "label": event.label})


def decode_event(body: bytes) -> ApplicationEvent:
    data = _load(body)
    try:
        application_id = UUID(data["application_id"])
        applicant_id = UUID(data["applicant_id"])
        at = datetime.fromisoformat(data["at"])
        match data["type"]:
            case "status_changed":
                expires_at = data.get("expires_at")
                return StatusChangedEvent(
                    application_id=application_id,
                    applicant_id=applicant_id,
                    from_status=ApplicationStatus(data["from_status"]),
                    to_status=ApplicationStatus(data["to_status"]),
                    reason=str(data["reason"]),
                    at=at,
                    expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
                )
            case "step":
                return PipelineStepEvent(
                    application_id=application_id,
                    applicant_id=applicant_id,
                    step=PipelineStep(data["step"]),
                    label=str(data["label"]),
                    at=at,
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedMessageError(f"Событие без нужных полей: {exc!r}") from exc
    raise MalformedMessageError(f"Неизвестный тип события: {data.get('type')!r}")


def _dump(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode()


def _load(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise MalformedMessageError("Тело сообщения не JSON") from exc
    if not isinstance(data, dict):
        raise MalformedMessageError("Тело сообщения не JSON-объект")
    return data
