from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.storage import LocalDocumentStore, StorageError, SupabaseDocumentStore


def test_local_store_round_trip(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)

    store.write("2026/10/doc.pdf", b"%PDF-1.4")

    assert store.read("2026/10/doc.pdf") == b"%PDF-1.4"


def test_local_store_never_overwrites(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path)
    store.write("doc.pdf", b"original")

    with pytest.raises(StorageError):
        store.write("doc.pdf", b"replacement")
    assert store.read("doc.pdf") == b"original"


def test_local_store_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        LocalDocumentStore(tmp_path).read("missing.pdf")


@pytest.fixture
def supabase_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "supabase_secret_key": SecretStr("sb_secret_test"),
            "supabase_url": "http://sb.test/",
        }
    )


def test_supabase_store_requests(supabase_settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"%PDF-stored")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = SupabaseDocumentStore(supabase_settings, client=client)

    store.write("a/b.pdf", b"%PDF-new")
    content = store.read("a/b.pdf")

    upload, download = requests
    assert str(upload.url) == "http://sb.test/storage/v1/object/invoices/a/b.pdf"
    assert upload.method == "POST"
    assert upload.headers["apikey"] == "sb_secret_test"
    assert upload.headers["x-upsert"] == "false"
    assert download.method == "GET"
    assert content == b"%PDF-stored"


def test_supabase_store_errors(supabase_settings: Settings) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    store = SupabaseDocumentStore(supabase_settings, client=client)

    with pytest.raises(StorageError):
        store.read("missing.pdf")
    with pytest.raises(StorageError):
        store.write("missing.pdf", b"%PDF")


def test_supabase_store_needs_a_key(settings: Settings) -> None:
    with pytest.raises(ValueError, match="SUPABASE_SECRET_KEY"):
        SupabaseDocumentStore(settings)
