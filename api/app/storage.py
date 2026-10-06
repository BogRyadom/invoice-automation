from pathlib import Path
from typing import Protocol

import httpx

from app.config import Settings


class StorageError(Exception):
    pass


# The object is already stored. Paths are content hashes, so the content is the same.
class AlreadyExists(StorageError):
    pass


class DocumentStore(Protocol):
    def read(self, path: str) -> bytes:
        """Bytes of a stored original document."""
        ...

    def write(self, path: str, data: bytes, content_type: str = "application/pdf") -> None:
        """Store an original document. Raises AlreadyExists instead of overwriting."""
        ...

    def signed_url(self, path: str, ttl_seconds: int) -> str:
        """Short-lived URL a browser can use to open the original."""
        ...


class LocalDocumentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> bytes:
        """Read a file below the root directory."""
        try:
            return (self.root / path).read_bytes()
        except OSError as exc:
            raise StorageError(f"cannot read {path}: {exc}") from exc

    def write(self, path: str, data: bytes, content_type: str = "application/pdf") -> None:
        """Write a new file below the root directory."""
        target = self.root / path
        if target.exists():
            raise AlreadyExists(f"{path} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def signed_url(self, path: str, ttl_seconds: int) -> str:
        """Local files have no signed URLs; a file URI is enough for tests and eval."""
        return (self.root / path).resolve().as_uri()


class SupabaseDocumentStore:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        key = settings.supabase_secret_key.get_secret_value()
        if not key:
            raise ValueError("SUPABASE_SECRET_KEY is not set")
        self.bucket = settings.supabase_storage_bucket
        self.api = f"{settings.supabase_url.rstrip('/')}/storage/v1"
        self.public_api = f"{settings.supabase_public_url.rstrip('/')}/storage/v1"
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"apikey": key}

    def read(self, path: str) -> bytes:
        """Download an object from the private bucket."""
        response = self.client.get(f"{self.api}/object/{self.bucket}/{path}", headers=self.headers)
        if response.status_code != 200:
            raise StorageError(f"download {path}: HTTP {response.status_code}")
        return response.content

    def write(self, path: str, data: bytes, content_type: str = "application/pdf") -> None:
        """Upload a new object; the bucket refuses to replace an existing one."""
        response = self.client.post(
            f"{self.api}/object/{self.bucket}/{path}",
            content=data,
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "false"},
        )
        if response.status_code == 200:
            return
        if response.status_code in (400, 409) and "KeyAlreadyExists" in response.text:
            raise AlreadyExists(f"{path} already exists")
        raise StorageError(f"upload {path}: HTTP {response.status_code}")

    def signed_url(self, path: str, ttl_seconds: int) -> str:
        """Signed download URL on the public Supabase address."""
        response = self.client.post(
            f"{self.api}/object/sign/{self.bucket}/{path}",
            json={"expiresIn": ttl_seconds},
            headers=self.headers,
        )
        if response.status_code != 200:
            raise StorageError(f"sign {path}: HTTP {response.status_code}")
        return f"{self.public_api}{response.json()['signedURL']}"
