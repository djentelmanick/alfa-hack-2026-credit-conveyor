import asyncio

from argon2 import PasswordHasher as Argon2
from argon2.exceptions import InvalidHashError, VerificationError

from app.domain.ports import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    def __init__(self, hasher: Argon2 | None = None) -> None:
        self._hasher = hasher or Argon2()

    async def hash(self, password: str) -> str:
        # argon2 намеренно долго считает хеш: в event loop это тормозило бы все запросы.
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except VerificationError, InvalidHashError:
            return False
