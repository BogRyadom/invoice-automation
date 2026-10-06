# Queue worker (docs/SPEC.md sections 3.7 and 10). Documents wait in Postgres; a worker claims
# one with FOR UPDATE SKIP LOCKED. A crashed worker leaves locked_at behind, and the document
# is claimed again after PROCESSING_TIMEOUT until WORKER_MAX_ATTEMPTS is reached.

import logging
import signal
import threading

import httpx
from sqlalchemy import Connection, Engine, text

from app import repository as repo
from app.config import Settings, get_settings
from app.db import create_db_engine, ping
from app.extraction.groq_provider import GroqProvider
from app.extraction.provider import ExtractionProvider
from app.log import configure_logging
from app.outbox import deliver_batch
from app.processing import ACTOR, ClaimedDocument, ProviderFatal, process_document
from app.status import change_status, record_event
from app.storage import DocumentStore, SupabaseDocumentStore

logger = logging.getLogger(__name__)

# After the provider rejects every request (bad key, daily quota) the worker pauses instead of
# failing the rest of the queue one document after another.
PROVIDER_FATAL_PAUSE_SECONDS = 600

STALE = "status = 'processing' AND locked_at < now() - make_interval(secs => :timeout)"


def expire_stale(conn: Connection, settings: Settings) -> int:
    """Fail documents whose processing crashed WORKER_MAX_ATTEMPTS times."""
    rows = conn.execute(
        text(
            f"""
            SELECT id, attempts FROM documents
            WHERE {STALE} AND attempts >= :max_attempts
            FOR UPDATE SKIP LOCKED
            """
        ),
        {
            "timeout": settings.processing_timeout_seconds,
            "max_attempts": settings.worker_max_attempts,
        },
    ).all()
    for row in rows:
        change_status(
            conn,
            row.id,
            "failed",
            actor=ACTOR,
            reason="processing_timeout",
            details={"attempts": row.attempts},
        )
        repo.add_outbox_event(
            conn, row.id, "failed", {"document_id": row.id, "reason": "processing_timeout"}
        )
    return len(rows)


def claim_next(conn: Connection, settings: Settings) -> ClaimedDocument | None:
    """Lock the next document to process: new, reprocessed or left behind by a crash."""
    row = conn.execute(
        text(
            f"""
            SELECT id, sha256, storage_path, received_at, status, locked_at, attempts
            FROM documents
            WHERE (status = 'received' AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
               OR (status = 'processing' AND locked_at IS NULL)
               OR ({STALE})
            ORDER BY received_at, created_at, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"timeout": settings.processing_timeout_seconds},
    ).one_or_none()
    if row is None:
        return None
    if row.status == "received":
        change_status(conn, row.id, "processing", actor=ACTOR)
    elif row.locked_at is not None:
        record_event(conn, row.id, "requeued", ACTOR, {"attempts": row.attempts})
    conn.execute(
        text("UPDATE documents SET locked_at = now(), attempts = attempts + 1 WHERE id = :id"),
        {"id": row.id},
    )
    return ClaimedDocument(
        id=row.id, sha256=row.sha256, storage_path=row.storage_path, received_at=row.received_at
    )


def run_once(
    engine: Engine, store: DocumentStore, provider: ExtractionProvider, settings: Settings
) -> bool:
    """Process at most one document. Returns False when the queue is empty."""
    with engine.begin() as conn:
        expire_stale(conn, settings)
        claimed = claim_next(conn, settings)
    if claimed is None:
        return False
    try:
        status = process_document(
            engine, claimed, store=store, provider=provider, settings=settings
        )
    except ProviderFatal:
        raise
    except Exception:
        # The lock stays; the document is retried after PROCESSING_TIMEOUT, bounded by attempts.
        logger.exception("processing document %s failed", claimed.id)
        return True
    logger.info("document %s -> %s", claimed.id, status)
    return True


def run(
    stop: threading.Event,
    engine: Engine,
    store: DocumentStore,
    provider: ExtractionProvider,
    settings: Settings,
    outbox_client: httpx.Client | None = None,
) -> int:
    """Process documents and deliver outbox events until stop is set. Returns documents handled."""
    handled = 0
    while not stop.is_set():
        delivered = deliver_batch(engine, outbox_client, settings) if outbox_client else 0
        try:
            busy = run_once(engine, store, provider, settings)
        except ProviderFatal as exc:
            logger.error("LLM provider unusable, pausing: %s", exc)
            stop.wait(PROVIDER_FATAL_PAUSE_SECONDS)
            continue
        if busy:
            handled += 1
        elif not delivered:
            stop.wait(settings.worker_poll_interval_seconds)
    return handled


def main() -> None:
    """Start the worker and stop gracefully on SIGINT or SIGTERM."""
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    ping(engine)
    store = SupabaseDocumentStore(settings)
    provider = GroqProvider(settings)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    logger.info("worker started")
    with httpx.Client() as outbox_client:
        run(stop, engine, store, provider, settings, outbox_client)
    engine.dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
