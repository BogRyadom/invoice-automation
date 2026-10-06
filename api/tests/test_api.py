import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.auth import TokenVerifier
from app.config import Settings
from app.main import create_app
from app.storage import LocalDocumentStore
from app.worker import run_once
from corpus.storage import DOCUMENTS_DIR, load_corpus, load_known_vendors
from eval.database import seed_vendors, temporary_database
from tests.fakes import ScriptedProvider

pytestmark = pytest.mark.db

DOCS = {doc.doc_id: doc for doc in load_corpus()}
ISSUER = "http://auth.test/auth/v1"
SECRET = "test-ingest-secret"
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def token(key: Any = PRIVATE_KEY, **claims: Any) -> str:
    payload = {
        "sub": "reviewer-1",
        "email": "reviewer@lumen-harbor.example",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 600,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="ES256")


AUTH = {"Authorization": f"Bearer {token()}"}


@pytest.fixture
def engine(admin_database_url: str) -> Iterator[Engine]:
    with temporary_database(admin_database_url, prefix="test") as engine:
        seed_vendors(engine, load_known_vendors())
        yield engine


@pytest.fixture
def api_settings(settings: Settings, engine: Engine) -> Settings:
    url = engine.url.render_as_string(hide_password=False)
    return settings.model_copy(
        update={"database_url": url, "ingest_shared_secret": SecretStr(SECRET)}
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path)


@pytest.fixture
def client(api_settings: Settings, store: LocalDocumentStore) -> Iterator[TestClient]:
    verifier = TokenVerifier(lambda _: PRIVATE_KEY.public_key(), ISSUER)
    app = create_app(api_settings, store=store, token_verifier=verifier)
    with TestClient(app) as test_client:
        yield test_client


def upload(
    client: TestClient, doc_id: str, message: str = "msg-1", secret: str | None = SECRET
) -> Any:
    doc = DOCS[doc_id]
    data = (DOCUMENTS_DIR / doc.filename).read_bytes()
    headers = {"X-Ingest-Secret": secret} if secret else {}
    return client.post(
        "/api/documents",
        headers=headers,
        files={"file": (doc.filename, data, "application/pdf")},
        data={
            "gmail_message_id": message,
            "attachment_id": "att-1",
            "sender": doc.email.sender,
            "subject": doc.email.subject,
            "received_at": doc.email.received_at.isoformat(),
        },
    )


def process(engine: Engine, store: LocalDocumentStore, settings: Settings, *answers: str) -> None:
    provider = ScriptedProvider(list(answers))
    while run_once(engine, store, provider, settings):
        pass


def printed(doc_id: str) -> str:
    value = DOCS[doc_id].printed
    assert value is not None
    return value.model_dump_json()


def values(doc_id: str, **changes: Any) -> dict[str, Any]:
    expected = DOCS[doc_id].expected
    printed_values = DOCS[doc_id].printed
    assert expected is not None and printed_values is not None
    base = {
        "vendor_name": printed_values.vendor_name_raw,
        "vendor_tax_id": expected.vendor_tax_id,
        "invoice_number": printed_values.invoice_number_raw,
        "invoice_date": expected.invoice_date.isoformat(),
        "due_date": expected.due_date.isoformat() if expected.due_date else None,
        "currency": expected.currency,
        "subtotal": str(expected.subtotal) if expected.subtotal is not None else None,
        "discount": None,
        "shipping": None,
        "tax_total": str(expected.tax_total) if expected.tax_total is not None else None,
        "total": str(expected.total),
        "line_items": [
            {
                "description": item.description,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price),
                "amount": str(item.amount),
            }
            for item in expected.line_items
        ],
    }
    return {**base, **changes}


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def in_review(
    client: TestClient, engine: Engine, store: LocalDocumentStore, settings: Settings, doc_id: str
) -> UUID:
    response = upload(client, doc_id)
    process(engine, store, settings, printed(doc_id))
    document_id = UUID(response.json()["document_id"])
    assert (
        scalar(engine, "SELECT status FROM documents WHERE id = :id", id=document_id)
        == "needs_review"
    )
    return document_id


@pytest.mark.parametrize(("secret", "code"), [(None, 401), ("wrong", 401)])
def test_ingest_requires_the_secret(client: TestClient, secret: str | None, code: int) -> None:
    assert upload(client, "clean_01", secret=secret).status_code == code


def test_ingest_is_unavailable_without_a_configured_secret(
    settings: Settings, engine: Engine, store: LocalDocumentStore
) -> None:
    unconfigured = settings.model_copy(
        update={"database_url": engine.url.render_as_string(hide_password=False)}
    )
    app = create_app(
        unconfigured, store=store, token_verifier=TokenVerifier(lambda _: None, ISSUER)
    )
    with TestClient(app) as test_client:
        assert upload(test_client, "clean_01", secret="anything").status_code == 503


def test_ingest_stores_the_original_and_is_idempotent(
    client: TestClient, engine: Engine, store: LocalDocumentStore
) -> None:
    first = upload(client, "clean_01")
    again = upload(client, "clean_01")
    other_message = upload(client, "clean_01", message="msg-2")

    assert first.status_code == 202 and first.json()["created"] is True
    assert again.status_code == 200 and again.json()["document_id"] == first.json()["document_id"]
    assert other_message.status_code == 202
    data = (DOCUMENTS_DIR / "clean_01.pdf").read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    assert store.read(f"originals/{sha[:2]}/{sha}") == data
    assert scalar(engine, "SELECT count(*) FROM documents") == 2
    assert scalar(engine, "SELECT count(*) FROM document_events WHERE event_type = 'received'") == 2


def test_ingest_rejects_large_and_empty_files(
    api_settings: Settings, store: LocalDocumentStore
) -> None:
    small = api_settings.model_copy(update={"ingest_max_file_mb": 1, "max_file_mb": 1})
    verifier = TokenVerifier(lambda _: PRIVATE_KEY.public_key(), ISSUER)
    with TestClient(create_app(small, store=store, token_verifier=verifier)) as test_client:
        common = {"gmail_message_id": "m", "received_at": "2026-10-01T09:00:00+00:00"}
        big = b"%PDF-" + b"0" * (1024 * 1024)
        too_large = test_client.post(
            "/api/documents",
            headers={"X-Ingest-Secret": SECRET},
            files={"file": ("big.pdf", big, "application/pdf")},
            data=common,
        )
        empty = test_client.post(
            "/api/documents",
            headers={"X-Ingest-Secret": SECRET},
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data=common,
        )
    assert too_large.status_code == 413
    assert empty.status_code == 422


@pytest.mark.parametrize(
    "bad_token",
    [
        token(OTHER_KEY),
        token(exp=int(time.time()) - 10),
        token(aud="anon"),
        token(iss="http://elsewhere.test/auth/v1"),
        "not-a-jwt",
    ],
    ids=["wrong-key", "expired", "audience", "issuer", "garbage"],
)
def test_review_endpoints_need_a_valid_token(client: TestClient, bad_token: str) -> None:
    assert client.get("/api/documents").status_code == 401
    response = client.get("/api/documents", headers={"Authorization": f"Bearer {bad_token}"})
    assert response.status_code == 401


def test_queue_and_document_page(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "clean_09")

    queue = client.get("/api/documents", params={"status": "needs_review"}, headers=AUTH).json()
    page = client.get(f"/api/documents/{document_id}", headers=AUTH).json()

    assert [(row["id"], row["vendor"], row["flag_count"]) for row in queue] == [
        (str(document_id), "Pellucid Analytics Ltd", 1)
    ]
    assert queue[0]["total"] == "2398.50"
    assert page["document"]["status"] == "needs_review"
    assert {c["check_id"] for c in page["checks"] if c["status"] == "fail"} == {"W4"}
    assert [e["payload"].get("to") for e in page["events"]] == [None, "processing", "needs_review"]
    assert page["pdf_url"].startswith("file:")


def test_approving_a_new_vendor(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "clean_09")

    response = client.post(
        f"/api/documents/{document_id}/approve", headers=AUTH, json={"values": values("clean_09")}
    )

    assert response.status_code == 200, response.text
    assert response.json()["approval_mode"] == "human"
    assert (
        scalar(engine, "SELECT count(*) FROM vendors WHERE normalized_name = 'pellucid analytics'")
        == 1
    )
    assert scalar(engine, "SELECT approved_by FROM invoices") == "reviewer@lumen-harbor.example"
    assert scalar(engine, "SELECT count(*) FROM review_edits") == 0
    payload = scalar(engine, "SELECT payload FROM outbox_events WHERE event_type = 'approved'")
    assert (payload["vendor"], payload["total"]) == ("Pellucid Analytics Ltd", "2398.50")


def test_new_spelling_of_a_known_vendor_becomes_an_alias(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "clean_09")
    brevik = scalar(engine, "SELECT id FROM vendors WHERE normalized_name = 'brevik software'")

    response = client.post(
        f"/api/documents/{document_id}/approve",
        headers=AUTH,
        json={"values": values("clean_09", vendor_name="Brevik Soft"), "vendor_id": str(brevik)},
    )

    assert response.status_code == 200, response.text
    assert scalar(engine, "SELECT normalized_alias FROM vendor_aliases") == "brevik soft"
    assert scalar(engine, "SELECT count(*) FROM review_edits WHERE field = 'vendor_name'") == 1


def test_resolved_date_format_is_remembered(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "european_01")
    halvren = scalar(
        engine, "SELECT id FROM vendors WHERE normalized_name = 'halvren maschinenbau'"
    )

    response = client.post(
        f"/api/documents/{document_id}/approve",
        headers=AUTH,
        json={"values": values("european_01"), "vendor_id": str(halvren), "date_format": "DMY"},
    )

    assert response.status_code == 200, response.text
    assert scalar(engine, "SELECT date_format FROM vendors WHERE id = :id", id=halvren) == "DMY"


def test_failing_check_needs_override_and_comment(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "arithmetic_01")
    ostberg = scalar(engine, "SELECT id FROM vendors WHERE normalized_name = 'ostberg facilities'")
    body = {"values": values("arithmetic_01"), "vendor_id": str(ostberg)}
    url = f"/api/documents/{document_id}/approve"

    refused = client.post(url, headers=AUTH, json=body)
    no_comment = client.post(url, headers=AUTH, json={**body, "override": True})
    accepted = client.post(
        url, headers=AUTH, json={**body, "override": True, "comment": "Vendor confirmed by phone"}
    )

    assert refused.status_code == 422
    assert [c["check_id"] for c in refused.json()["detail"]["checks"]] == ["H2"]
    assert no_comment.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["approval_mode"] == "human_override"
    event = scalar(
        engine,
        "SELECT payload FROM document_events WHERE payload->>'to' = 'approved'",
    )
    assert event["comment"] == "Vendor confirmed by phone"


def test_corrected_value_is_recorded_as_an_edit(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "arithmetic_01")
    ostberg = scalar(engine, "SELECT id FROM vendors WHERE normalized_name = 'ostberg facilities'")

    response = client.post(
        f"/api/documents/{document_id}/approve",
        headers=AUTH,
        json={"values": values("arithmetic_01", total="585.75"), "vendor_id": str(ostberg)},
    )

    assert response.status_code == 200, response.text
    with engine.connect() as conn:
        edit = conn.execute(
            text("SELECT field, extracted_value, final_value FROM review_edits")
        ).one()
    assert tuple(edit) == ("total", "558.75", "585.75")


def test_duplicate_invoice_cannot_be_approved_twice(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    first = in_review(client, engine, store, api_settings, "clean_09")
    approve = {"values": values("clean_09")}
    assert (
        client.post(f"/api/documents/{first}/approve", headers=AUTH, json=approve).status_code
        == 200
    )
    second = in_review(client, engine, store, api_settings, "clean_10")
    pellucid = scalar(engine, "SELECT id FROM vendors WHERE normalized_name = 'pellucid analytics'")

    response = client.post(
        f"/api/documents/{second}/approve",
        headers=AUTH,
        json={
            "values": values("clean_09"),
            "vendor_id": str(pellucid),
            "override": True,
            "comment": "x",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["document_id"] == str(first)


def test_reject_and_reprocess(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "clean_09")

    assert (
        client.post(
            f"/api/documents/{document_id}/reject", headers=AUTH, json={"reason": ""}
        ).status_code
        == 422
    )
    assert client.post(f"/api/documents/{document_id}/reprocess", headers=AUTH).status_code == 409
    rejected = client.post(
        f"/api/documents/{document_id}/reject", headers=AUTH, json={"reason": "Not ours"}
    )
    again = client.post(
        f"/api/documents/{document_id}/reject", headers=AUTH, json={"reason": "Not ours"}
    )

    assert rejected.json()["status"] == "rejected"
    assert again.status_code == 409


def test_failed_document_can_be_reprocessed_or_entered_manually(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    response = upload(client, "clean_01")
    process(engine, store, api_settings, "{}", "{}")
    document_id = response.json()["document_id"]

    reprocessed = client.post(f"/api/documents/{document_id}/reprocess", headers=AUTH)
    attempts = scalar(engine, "SELECT attempts FROM documents WHERE id = :id", id=document_id)
    process(engine, store, api_settings, "{}", "{}")
    quorrin = scalar(
        engine, "SELECT id FROM vendors WHERE normalized_name = 'quorrin office supply'"
    )
    manual = client.post(
        f"/api/documents/{document_id}/approve",
        headers=AUTH,
        json={"values": values("clean_01"), "vendor_id": str(quorrin)},
    )

    assert reprocessed.json()["status"] == "processing"
    assert attempts == 0
    assert manual.status_code == 200, manual.text
    assert manual.json()["approval_mode"] == "manual_entry"


def test_export_and_stats(
    client: TestClient, engine: Engine, store: LocalDocumentStore, api_settings: Settings
) -> None:
    document_id = in_review(client, engine, store, api_settings, "clean_09")
    client.post(
        f"/api/documents/{document_id}/approve", headers=AUTH, json={"values": values("clean_09")}
    )

    csv = client.get("/api/invoices/export.csv", params={"date_from": "2026-07-01"}, headers=AUTH)
    empty = client.get("/api/invoices/export.csv", params={"date_to": "2026-01-01"}, headers=AUTH)
    stats = client.get("/api/stats", headers=AUTH).json()

    header, row = csv.text.strip().split("\n")
    assert header.startswith("invoice_id,document_id,vendor,vendor_tax_id,invoice_number")
    assert "Pellucid Analytics Ltd" in row and "2398.50" in row
    assert empty.text.strip().count("\n") == 0
    assert stats["documents_by_status"] == {"approved": 1}
    assert stats["review_rate"] == {"hits": 1, "total": 1}
    assert stats["outbox"] == {"pending": 2, "undeliverable": 0}
    assert json.dumps(stats)
