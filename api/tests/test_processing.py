import hashlib
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.config import Settings
from app.extraction.provider import ProviderUnavailable
from app.processing import ProviderFatal
from app.status import change_status
from app.storage import LocalDocumentStore, StorageError
from app.worker import claim_next, run_once
from corpus.storage import DOCUMENTS_DIR, load_corpus, load_known_vendors
from eval.database import seed_vendors, temporary_database
from tests.fakes import NOT_AN_INVOICE, ScriptedProvider

pytestmark = pytest.mark.db

DOCS = {doc.doc_id: doc for doc in load_corpus()}
STORE = LocalDocumentStore(DOCUMENTS_DIR)


class BrokenStore:
    def read(self, path: str) -> bytes:
        raise StorageError(f"storage down for {path}")

    def write(self, path: str, data: bytes) -> None:
        raise StorageError("storage down")


@pytest.fixture
def engine(admin_database_url: str) -> Iterator[Engine]:
    with temporary_database(admin_database_url, prefix="test") as engine:
        seed_vendors(engine, load_known_vendors())
        yield engine


@pytest.fixture
def auto(settings: Settings) -> Settings:
    return settings.model_copy(update={"auto_approve_enabled": True})


def answer(doc_id: str) -> str:
    printed = DOCS[doc_id].printed
    assert printed is not None
    return printed.model_dump_json()


def add_document(engine: Engine, doc_id: str, message: str | None = None) -> UUID:
    doc = DOCS[doc_id]
    data = (DOCUMENTS_DIR / doc.filename).read_bytes()
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO documents (gmail_message_id, filename, sha256, storage_path,
                                       received_at)
                VALUES (:message, :filename, :sha256, :path, :received_at)
                RETURNING id
                """
            ),
            {
                "message": message or f"msg-{doc_id}",
                "filename": doc.filename,
                "sha256": hashlib.sha256(data).hexdigest(),
                "path": doc.filename,
                "received_at": doc.email.received_at,
            },
        ).scalar_one()


def state(engine: Engine, document_id: UUID) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM documents WHERE id = :id"), {"id": document_id}
        ).one()
        return dict(row._mapping)


def count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def flags(engine: Engine, document_id: UUID) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    """
                    SELECT c.check_id FROM check_results c
                    JOIN extractions e ON e.id = c.extraction_id
                    WHERE e.document_id = :id AND c.status = 'fail'
                    """
                ),
                {"id": document_id},
            ).scalars()
        )


def test_empty_queue(engine: Engine, settings: Settings) -> None:
    assert run_once(engine, STORE, ScriptedProvider([]), settings) is False


def test_clean_invoice_is_auto_approved(engine: Engine, auto: Settings) -> None:
    document_id = add_document(engine, "clean_01")

    assert run_once(engine, STORE, ScriptedProvider([answer("clean_01")]), auto)

    doc = state(engine, document_id)
    assert (doc["status"], doc["extraction_path"], doc["attempts"]) == ("auto_approved", "text", 1)
    assert doc["locked_at"] is None
    assert flags(engine, document_id) == set()
    sql = "SELECT count(*) FROM invoices WHERE document_id = :id AND approval_mode = 'auto'"
    assert count(engine, sql, id=document_id) == 1
    assert count(engine, "SELECT count(*) FROM invoice_line_items") == 4
    assert count(engine, "SELECT count(*) FROM check_results") == 15
    sql = "SELECT count(*) FROM outbox_events WHERE event_type = 'auto_approved'"
    assert count(engine, sql) == 1
    sql = "SELECT count(*) FROM document_events WHERE document_id = :id"
    assert count(engine, sql, id=document_id) == 2


def test_auto_approve_disabled_sends_to_review(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "clean_01")

    run_once(engine, STORE, ScriptedProvider([answer("clean_01")]), settings)

    assert state(engine, document_id)["status"] == "needs_review"
    assert count(engine, "SELECT count(*) FROM invoices") == 0


def test_same_file_twice_is_skipped_without_llm(engine: Engine, auto: Settings) -> None:
    first = add_document(engine, "clean_01", message="msg-1")
    second = add_document(engine, "clean_01", message="msg-2")
    provider = ScriptedProvider([answer("clean_01")])

    run_once(engine, STORE, provider, auto)
    run_once(engine, STORE, provider, auto)

    doc = state(engine, second)
    assert (doc["status"], doc["skip_reason"]) == ("skipped", "duplicate_file")
    assert doc["duplicate_of_document_id"] == first
    assert len(provider.calls) == 1


def test_duplicate_of_an_invoice_waiting_for_review(engine: Engine, auto: Settings) -> None:
    first = add_document(engine, "clean_01")
    second = add_document(engine, "clean_02")
    provider = ScriptedProvider([answer("clean_11"), answer("clean_11")])

    run_once(engine, STORE, provider, auto)
    run_once(engine, STORE, provider, auto)

    assert state(engine, first)["status"] == "needs_review"
    assert "H5" not in flags(engine, first)
    assert "H5" in flags(engine, second)


@pytest.mark.parametrize(
    ("doc_id", "model_answer", "reason"),
    [
        ("not_invoice_03", NOT_AN_INVOICE, "not_invoice"),
        ("clean_01", NOT_AN_INVOICE.replace('"other"', '"credit_note"'), "unsupported_type"),
    ],
)
def test_non_invoices_are_skipped(
    engine: Engine, settings: Settings, doc_id: str, model_answer: str, reason: str
) -> None:
    document_id = add_document(engine, doc_id)

    run_once(engine, STORE, ScriptedProvider([model_answer]), settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["skip_reason"]) == ("skipped", reason)
    assert count(engine, "SELECT count(*) FROM extractions") == 1


def test_invalid_answers_fail_and_keep_the_raw_output(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "clean_01")

    run_once(engine, STORE, ScriptedProvider(["{}", "still not valid"]), settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["failure_reason"]) == ("failed", "invalid_extraction")
    assert doc["last_error"]
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT raw_output FROM extractions")).scalar_one()
    assert [c["content"] for c in raw["completions"]] == ["{}", "still not valid"]
    sql = "SELECT count(*) FROM outbox_events WHERE event_type = 'failed'"
    assert count(engine, sql) == 1


def test_encrypted_pdf_fails_without_llm(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "broken_02")
    provider = ScriptedProvider([])

    run_once(engine, STORE, provider, settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["failure_reason"]) == ("failed", "encrypted_pdf")
    assert provider.calls == []


def test_fatal_provider_error_stops_the_worker(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "clean_01")
    provider = ScriptedProvider([ProviderUnavailable("daily quota", fatal=True)])

    with pytest.raises(ProviderFatal):
        run_once(engine, STORE, provider, settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["failure_reason"]) == ("failed", "llm_unavailable")


def expire_lock(engine: Engine, document_id: UUID) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE documents SET locked_at = now() - interval '1 hour' WHERE id = :id"),
            {"id": document_id},
        )


def test_crashed_processing_is_retried_then_times_out(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "clean_01")
    provider = ScriptedProvider([])

    for attempt in (1, 2, 3):
        assert run_once(engine, BrokenStore(), provider, settings)
        doc = state(engine, document_id)
        assert (doc["status"], doc["attempts"]) == ("processing", attempt)
        expire_lock(engine, document_id)

    run_once(engine, BrokenStore(), provider, settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["failure_reason"]) == ("failed", "processing_timeout")
    sql = "SELECT count(*) FROM document_events WHERE event_type = 'requeued'"
    assert count(engine, sql) == 2


def test_live_lock_is_not_stolen(engine: Engine, settings: Settings) -> None:
    add_document(engine, "clean_01")
    run_once(engine, BrokenStore(), ScriptedProvider([]), settings)

    assert run_once(engine, STORE, ScriptedProvider([]), settings) is False


def test_concurrent_workers_claim_different_documents(engine: Engine, settings: Settings) -> None:
    first = add_document(engine, "clean_01")
    second = add_document(engine, "clean_02")

    with engine.connect() as one, engine.connect() as two, one.begin(), two.begin():
        claimed_one = claim_next(one, settings)
        claimed_two = claim_next(two, settings)

    assert claimed_one is not None and claimed_two is not None
    assert {claimed_one.id, claimed_two.id} == {first, second}


def test_reprocessed_document_is_picked_up_again(engine: Engine, settings: Settings) -> None:
    document_id = add_document(engine, "clean_01")
    run_once(engine, STORE, ScriptedProvider(["{}", "{}"]), settings)
    with engine.begin() as conn:
        change_status(conn, document_id, "processing", actor="reviewer")

    run_once(engine, STORE, ScriptedProvider([answer("clean_01")]), settings)

    doc = state(engine, document_id)
    assert (doc["status"], doc["failure_reason"]) == ("needs_review", None)
