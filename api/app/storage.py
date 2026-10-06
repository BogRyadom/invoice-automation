from pathlib import Path
from typing import Protocol

import httpx

from app.config import Settings


class DocumentStore(Protocol):
    def read(self, path: str) -> bytes:
        """Bytes of a stored original document."""
        ...

    def write(self, path: str, data: bytes) -> None:
        """Store an original document. Existing objects are never overwritten."""
        ...


class StorageError(Exception):
    pass


class LocalDocumentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> bytes:
        """Read a file below the root directory."""
        try:
            return (self.root / path).read_bytes()
        except OSError as exc:
            raise StorageError(f"cannot read {path}: {exc}") from exc

    def write(self, path: str, data: bytes) -> None:
        """Write a new file below the root directory."""
        target = self.root / path
        if target.exists():
            raise StorageError(f"{path} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class SupabaseDocumentStore:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        key = settings.supabase_secret_key.get_secret_value()
        if not key:
            raise ValueError("SUPABASE_SECRET_KEY is not set")
        bucket = settings.supabase_storage_bucket
        self.base = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}"
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"apikey": key}

    def read(self, path: str) -> bytes:
        """Download an object from the private bucket."""
        response = self.client.get(f"{self.base}/{path}", headers=self.headers)
        if response.status_code != 200:
            raise StorageError(f"download {path}: HTTP {response.status_code}")
        return response.content

    def write(self, path: str, data: bytes) -> None:
        """Upload a new object; the bucket refuses to replace an existing one."""
        response = self.client.post(
            f"{self.base}/{path}",
            content=data,
            headers={**self.headers, "Content-Type": "application/pdf", "x-upsert": "false"},
        )
        if response.status_code != 200:
            raise StorageError(f"upload {path}: HTTP {response.status_code}")
