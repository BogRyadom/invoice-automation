import base64
from datetime import UTC, datetime
from email import message_from_bytes
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.config import Settings
from corpus.storage import document_path, load_corpus
from scripts import demo_send, demo_vendors, gmail_seed

DOCS = {doc.doc_id: doc for doc in load_corpus()}
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def test_invoice_email_carries_the_pdf() -> None:
    doc = DOCS["clean_01"]

    message = gmail_seed.invoice_message(doc, "demo@example.com", NOW)
    parsed = message_from_bytes(base64.urlsafe_b64decode(gmail_seed.raw(message)))
    [attachment] = [part for part in parsed.walk() if part.get_filename()]

    assert parsed["From"] == doc.email.sender
    assert parsed["Subject"] == doc.email.subject
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == "clean_01.pdf"
    assert attachment.get_payload(decode=True) == document_path(doc).read_bytes()


def test_email_without_attachment() -> None:
    message = gmail_seed.message_without_attachment("demo@example.com", NOW)

    assert not [part for part in message.walk() if part.get_filename()]


def test_demo_send_posts_each_document_like_n8n(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"document_id": "d", "status": "received", "created": True})

    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    secret = settings.model_copy(update={"ingest_shared_secret": SecretStr("s3cret")})
    monkeypatch.setattr(demo_send, "get_settings", lambda: secret)
    monkeypatch.setattr(demo_send.httpx, "Client", client)

    assert demo_send.main(["--docs", "clean_01", "scan_01", "--api-url", "http://api.test"]) == 0

    assert [r.url.path for r in requests] == ["/api/documents", "/api/documents"]
    assert all(r.headers["X-Ingest-Secret"] == "s3cret" for r in requests)
    body = requests[0].content
    assert b'name="gmail_message_id"' in body and b"demo-clean_01" in body
    assert b'filename="clean_01.pdf"' in body


@pytest.mark.db
def test_demo_vendors_are_seeded_once(migrated_engine: Engine) -> None:
    first = demo_vendors.seed(migrated_engine)
    second = demo_vendors.seed(migrated_engine)

    with migrated_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM vendors")).scalar_one()
    assert (first, second, count) == (6, 0, 6)
