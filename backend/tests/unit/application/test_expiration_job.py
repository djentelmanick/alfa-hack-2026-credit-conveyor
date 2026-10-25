"""Одобрение и контрпредложение действуют 7 дней, потом заявка отменяется (SCOPE, раздел 8)."""

from datetime import timedelta

import pytest

from app.application.expiration_job import OFFER_EXPIRED
from app.domain.entities import Application
from app.domain.enums import ApplicationStatus
from app.domain.events import StatusChangedEvent
from tests.fakes.auth import FixedClock
from tests.fakes.context import make_application
from tests.fakes.pipeline import OFFER_TTL, make_expiration_job
from tests.fakes.runtime import RecordingPublisher
from tests.fakes.scenarios import NOW
from tests.fakes.uow import InMemoryStore

S = ApplicationStatus


async def _offered(store: InMemoryStore, status: ApplicationStatus) -> Application:
    app = make_application()
    for target in (S.SUBMITTED, S.CHECKING, S.SCORING):
        app.transition_to(target, actor_id=None, reason="", now=NOW)
    app.transition_to(status, actor_id=None, reason="", now=NOW, offer_ttl=OFFER_TTL)
    app.pull_events()
    await store.applications.add(app)
    return app


@pytest.mark.parametrize("status", [S.APPROVED, S.COUNTER_OFFER])
async def test_offer_is_alive_until_the_last_second(status: ApplicationStatus) -> None:
    store = InMemoryStore()
    clock = FixedClock(NOW + timedelta(days=7) - timedelta(seconds=1))
    publisher = RecordingPublisher(store)
    app = await _offered(store, status)

    assert await make_expiration_job(store.uow, clock, publisher).run_once() == 0

    assert app.status is status
    assert publisher.events == []


@pytest.mark.parametrize("status", [S.APPROVED, S.COUNTER_OFFER])
async def test_offer_is_cancelled_after_7_days(status: ApplicationStatus) -> None:
    store = InMemoryStore()
    clock = FixedClock(NOW + timedelta(days=7))
    publisher = RecordingPublisher(store)
    app = await _offered(store, status)

    assert await make_expiration_job(store.uow, clock, publisher).run_once() == 1

    assert app.status is S.CANCELLED
    (event,) = publisher.events
    assert isinstance(event, StatusChangedEvent)
    assert (event.from_status, event.to_status, event.reason) == (
        status,
        S.CANCELLED,
        OFFER_EXPIRED,
    )
    assert event.is_final
    assert publisher.commits_at_publish == [1]
