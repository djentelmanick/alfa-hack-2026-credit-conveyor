from app.domain.errors import NotFound, StorageUnavailable
from app.domain.ports import FileStoragePort, StoredFile

PRESIGNED_HOST = "http://files.test"


class InMemoryFileStorage(FileStoragePort):
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.broken = False

    async def put(self, bucket: str, key: str, data: bytes, content_type: str) -> StoredFile:
        self._raise_if_broken()
        self.objects[bucket, key] = data
        return StoredFile(bucket=bucket, key=key, size=len(data), content_type=content_type)

    async def get(self, bucket: str, key: str) -> bytes:
        self._raise_if_broken()
        try:
            return self.objects[bucket, key]
        except KeyError:
            raise NotFound("Файл не найден") from None

    async def delete(self, bucket: str, key: str) -> None:
        self._raise_if_broken()
        self.objects.pop((bucket, key), None)

    async def presigned_get(self, bucket: str, key: str, ttl_sec: int, file_name: str) -> str:
        self._raise_if_broken()
        return f"{PRESIGNED_HOST}/{bucket}/{key}?ttl={ttl_sec}&name={file_name}"

    def keys(self, bucket: str) -> list[str]:
        return [key for b, key in self.objects if b == bucket]

    def _raise_if_broken(self) -> None:
        if self.broken:
            raise StorageUnavailable()
