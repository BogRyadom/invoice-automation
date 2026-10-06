from uuid import UUID

import pytest
from sqlalchemy import Connection, text

from app.status import TRANSITIONS, InvalidTransition, change_status, check_transition

ALLOWED = [(current, target) for current, targets in TRANSITIONS.items() for target in targets]
FORBIDDEN = [
    ("received", "approved"),
    ("received", "needs_review"),
    ("processing", "approved"),
    ("needs_review", "exported"),
    ("needs_review", "auto_approved"),
    ("auto_approved", "approved"),
    ("approved", "rejected"),
    ("rejected", "approved"),
    ("skipped", "processing"),
    ("exported", "approved"),
    ("failed", "needs_review"),
]


@pytest.mark.parametrize(("current", "target"), ALLOWED)
def test_allowed_transitions(current: str, target: str) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_forbidden_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition):
        check_transition(current, target)


def test_spec_transitions_are_all_allowed() -> None:
    spec = {
        ("received", "processing"),
        ("processing", "skipped"),
        ("processing", "failed"),
        ("processing", "needs_review"),
        ("processing", "auto_approved"),
        ("needs_review", "approved"),
        ("needs_review", "rejected"),
        ("approved", "exported"),
        ("auto_approved", "exported"),
        ("failed", "processing"),
        ("failed", "approved"),
    }
    assert set(ALLOWED) == spec


def new_document(db: Connection, status: str = "received") -> UUID:
    return db.execute(
        text(
            """
            INSERT INTO documents (gmail_message_id, filename, sha256, storage_path, received_at,
                                   status)
            VALUES ('msg', 'a.pdf', repeat('a', 64), 'a.pdf', now(), :status)
            RETURNING id
            """
        ),
        {"status": status},
    ).scalar_one()


def events(db: Connection, document_id: UUID) -> list[dict]:
    return list(
        db.execute(
            text("SELECT payload FROM document_events WHERE document_id = :id ORDER BY created_at"),
            {"id": document_id},
        ).scalars()
    )


@pytest.mark.db
def test_change_status_records_an_event(db: Connection) -> None:
    document_id = new_document(db)

    previous = change_status(db, document_id, "processing", actor="worker")

    assert previous == "received"
    assert events(db, document_id) == [{"from": "received", "to": "processing"}]


@pytest.mark.db
def test_invalid_transition_changes_nothing(db: Connection) -> None:
    document_id = new_document(db)

    with pytest.raises(InvalidTransition):
        change_status(db, document_id, "approved", actor="reviewer")

    status = db.execute(
        text("SELECT status FROM documents WHERE id = :id"), {"id": document_id}
    ).scalar_one()
    assert status == "received"
    assert events(db, document_id) == []


@pytest.mark.db
def test_reasons_are_stored_and_cleared_on_reprocess(db: Connection) -> None:
    document_id = new_document(db, status="processing")
    change_status(db, document_id, "failed", actor="worker", reason="llm_unavailable")

    change_status(db, document_id, "processing", actor="reviewer")

    row = db.execute(
        text("SELECT status, failure_reason, locked_at FROM documents WHERE id = :id"),
        {"id": document_id},
    ).one()
    assert (row.status, row.failure_reason, row.locked_at) == ("processing", None, None)
    assert events(db, document_id)[0] == {
        "from": "processing",
        "to": "failed",
        "reason": "llm_unavailable",
    }
