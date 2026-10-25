from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar

from app.domain.enums import AllowedAction, ApplicationStatus, Role
from app.domain.errors import InvalidStatus

if TYPE_CHECKING:
    from app.domain.entities.application import Application

S = ApplicationStatus

FINAL_STATUSES = frozenset({S.ISSUED, S.REJECTED, S.CANCELLED})
EDITABLE_STATUSES = frozenset({S.DRAFT, S.NEEDS_INFO})
OFFER_STATUSES = frozenset({S.APPROVED, S.COUNTER_OFFER})


class ApplicationStateMachine:
    """Таблица переходов из BACKEND_SPEC, раздел 5. Всё, чего в ней нет, запрещено."""

    TRANSITIONS: ClassVar[Mapping[ApplicationStatus, frozenset[ApplicationStatus]]] = {
        S.DRAFT: frozenset({S.SUBMITTED, S.CANCELLED}),
        S.SUBMITTED: frozenset({S.CHECKING}),
        S.CHECKING: frozenset({S.REJECTED, S.NEEDS_INFO, S.MANUAL_REVIEW, S.SCORING}),
        S.NEEDS_INFO: frozenset({S.SUBMITTED, S.CANCELLED}),
        S.SCORING: frozenset({S.APPROVED, S.COUNTER_OFFER, S.MANUAL_REVIEW, S.REJECTED}),
        S.MANUAL_REVIEW: frozenset(
            {S.APPROVED, S.COUNTER_OFFER, S.NEEDS_INFO, S.REJECTED, S.CHECKING}
        ),
        S.APPROVED: frozenset({S.SIGNING, S.CANCELLED}),
        S.COUNTER_OFFER: frozenset({S.SIGNING, S.NEEDS_INFO, S.CANCELLED}),
        S.SIGNING: frozenset({S.ISSUED, S.CANCELLED}),
    }

    _CLIENT_ACTIONS: ClassVar[Mapping[ApplicationStatus, tuple[AllowedAction, ...]]] = {
        S.DRAFT: (
            AllowedAction.EDIT,
            AllowedAction.UPLOAD_STATEMENT,
            AllowedAction.SUBMIT,
            AllowedAction.CANCEL,
        ),
        S.NEEDS_INFO: (
            AllowedAction.EDIT,
            AllowedAction.UPLOAD_STATEMENT,
            AllowedAction.SUBMIT,
            AllowedAction.CANCEL,
        ),
        S.APPROVED: (AllowedAction.ACCEPT, AllowedAction.CANCEL),
        S.COUNTER_OFFER: (AllowedAction.ACCEPT, AllowedAction.CANCEL),
        S.SIGNING: (AllowedAction.SIGN, AllowedAction.CANCEL),
    }

    @classmethod
    def can_transition(cls, current: ApplicationStatus, target: ApplicationStatus) -> bool:
        return target in cls.TRANSITIONS.get(current, frozenset())

    @classmethod
    def transition(cls, current: ApplicationStatus, target: ApplicationStatus) -> ApplicationStatus:
        if not cls.can_transition(current, target):
            raise InvalidStatus(f"Переход {current} → {target} недоступен")
        return target

    @staticmethod
    def is_active(status: ApplicationStatus) -> bool:
        return status not in FINAL_STATUSES

    @classmethod
    def allowed_actions(cls, app: Application, role: Role) -> list[AllowedAction]:
        # Все действия списка клиентские: решения аналитика идут отдельными эндпоинтами.
        if role is not Role.CLIENT:
            return []
        return list(cls._CLIENT_ACTIONS.get(app.status, ()))
