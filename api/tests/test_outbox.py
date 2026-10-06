import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.config import Settings
from app.outbox import deliver_batch, deliver_next
from app.storage import LocalDocumentStore
from app.worker import run_once
from corpus.storage import DOCUMENTS_DIR, load_corpus, load_known_vendors
from eval.database import seed_vendors, temporary_database
from eval.predictors import insert_document
from tests.fakes import ScriptedProvider

pytestmark = pytest.mark.db

DOCS = {doc.doc_id: doc for doc in load_corpus()}
WEBHOOK = "http://n8n.test/webhook/invoice-events"


@pytest.fixture
def engine(admin_database_url: str) -> Iterator[Engine]:
    with temporary_database(admin_database_url, prefix="test") as engine:
        seed_vendors(engine, load_known_vendors())
        yield engine


@pytest.fixture
def hook_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "n8n_webhook_url": WEBHOOK,
            "n8n_webhook_secret": SecretStr("hook-secret"),
            "auto_approve_enabled": True,
        }
    )


def auto_approved(engine: Engine, settings: Settings) -> None:
    doc = DOCS["clean_01"]
    insert_document(engine, doc)
    assert doc.printed is not None
    provider = ScriptedProvider([doc.printed.model_dump_json()])
    run_once(engine, LocalDocumentStore(DOCUMENTS_DIR), provider, settings)


def client(responses: list[Any], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"ok": True, "exported": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


def outbox(engine: Engine) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT event_type, attempts, delivered_at, last_error FROM outbox_events")
        ).one()


def status(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT status FROM documents")).scalar_one()


def test_delivery_marks_the_invoice_exported(engine: Engine, hook_settings: Settings) -> None:
    auto_approved(engine, hook_settings)
    seen: list[httpx.Request] = []

    assert deliver_next(engine, client([200], seen), hook_settings)

    event = outbox(engine)
    assert (event.event_type, event.attempts, event.last_error) == ("auto_approved", 1, None)
    assert event.delivered_at is not None
    assert status(engine) == "exported"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT exported_at FROM invoices")).scalar_one() is not None
    [request] = seen
    body = json.loads(request.content)
    assert request.headers["X-Webhook-Secret"] == "hook-secret"
    assert body["event_type"] == "auto_approved"
    assert body["payload"]["total"] == "482.47"
    assert body["review_url"].endswith(f"/documents/{body['document_id']}")


def test_failed_delivery_is_retried_later_without_touching_the_document(
    engine: Engine, hook_settings: Settings
) -> None:
    auto_approved(engine, hook_settings)
    seen: list[httpx.Request] = []

    deliver_next(engine, client([503], seen), hook_settings)

    event = outbox(engine)
    assert (event.attempts, event.delivered_at, event.last_error) == (1, None, "HTTP 503")
    assert status(engine) == "auto_approved"
    assert deliver_next(engine, client([200], seen), hook_settings) is False


def test_delivery_attempts_are_bounded(engine: Engine, hook_settings: Settings) -> None:
    auto_approved(engine, hook_settings)
    seen: list[httpx.Request] = []
    failing = client([httpx.ConnectError("down") for _ in range(10)], seen)

    for _ in range(hook_settings.outbox_max_attempts):
        with engine.begin() as conn:
            conn.execute(text("UPDATE outbox_events SET next_attempt_at = now()"))
        deliver_next(engine, failing, hook_settings)

    with engine.begin() as conn:
        conn.execute(text("UPDATE outbox_events SET next_attempt_at = now()"))
    assert deliver_next(engine, failing, hook_settings) is False
    assert outbox(engine).attempts == hook_settings.outbox_max_attempts
    assert len(seen) == hook_settings.outbox_max_attempts


def test_delivery_without_sheets_does_not_export(engine: Engine, hook_settings: Settings) -> None:
    auto_approved(engine, hook_settings)
    no_sheets = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"ok": True, "exported": False})
        )
    )

    assert deliver_next(engine, no_sheets, hook_settings)

    assert outbox(engine).delivered_at is not None
    assert status(engine) == "auto_approved"


def test_nothing_is_sent_without_a_webhook(engine: Engine, settings: Settings) -> None:
    auto_approved(engine, settings.model_copy(update={"auto_approve_enabled": True}))
    seen: list[httpx.Request] = []

    assert deliver_batch(engine, client([], seen), settings) == 0
    assert seen == []
