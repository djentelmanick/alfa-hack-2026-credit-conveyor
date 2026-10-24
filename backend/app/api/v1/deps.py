from dataclasses import dataclass
from typing import Protocol

from fastapi import Request


class HealthProbe(Protocol):
    async def check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Container:
    """Зависимости, которые main.py собирает на старте и кладёт в app.state."""

    db_probe: HealthProbe
    s3_probe: HealthProbe


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_db_probe(request: Request) -> HealthProbe:
    return get_container(request).db_probe


def get_s3_probe(request: Request) -> HealthProbe:
    return get_container(request).s3_probe
