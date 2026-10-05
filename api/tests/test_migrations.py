from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db

TABLES = {
    "documents",
    "extractions",
    "check_results",
    "vendors",
    "vendor_aliases",
    "invoices",
    "invoice_line_items",
    "review_edits",
    "document_events",
    "outbox_events",
}

MONEY_COLUMNS = {
    ("invoices", "subtotal"),
    ("invoices", "discount"),
    ("invoices", "shipping"),
    ("invoices", "tax_total"),
    ("invoices", "total"),
    ("invoice_line_items", "quantity"),
    ("invoice_line_items", "unit_price"),
    ("invoice_line_items", "amount"),
}

SHA_A = "a" * 64


def insert_document(db: Connection, message_id: str = "msg-1", sha256: str = SHA_A) -> UUID:
    return db.execute(
        text(
            """
            INSERT INTO documents (gmail_message_id, filename, sha256, storage_path, received_at)
            VALUES (:message_id, 'invoice.pdf', :sha256, :storage_path, now())
            RETURNING id
            """
        ),
        {"message_id": message_id, "sha256": sha256, "storage_path": f"{message_id}/{sha256}.pdf"},
    ).scalar_one()


def insert_vendor(db: Connection, normalized_name: str = "acme supplies") -> UUID:
    return db.execute(
        text(
            """
            INSERT INTO vendors (canonical_name, normalized_name)
            VALUES (:name, :name)
            RETURNING id
            """
        ),
        {"name": normalized_name},
    ).scalar_one()


def insert_invoice(
    db: Connection,
    document_id: UUID,
    vendor_id: UUID,
    number: str = "INV2026001",
    currency: str = "USD",
) -> UUID:
    return db.execute(
        text(
            """
            INSERT INTO invoices (document_id, vendor_id, invoice_number,
                                  invoice_number_normalized, invoice_date, currency, total,
                                  approval_mode, approved_by)
            VALUES (:document_id, :vendor_id, :number, :number, DATE '2026-03-04', :currency,
                    1320.00, 'human', 'reviewer')
            RETURNING id
            """
        ),
        {
            "document_id": document_id,
            "vendor_id": vendor_id,
            "number": number,
            "currency": currency,
        },
    ).scalar_one()


def test_all_tables_exist(db: Connection) -> None:
    tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))

    assert set(tables.scalars()) == TABLES


def test_rls_enabled_on_every_table(db: Connection) -> None:
    without_rls = db.execute(
        text(
            """
            SELECT relname FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND NOT relrowsecurity
            """
        )
    )

    assert list(without_rls.scalars()) == []


def test_money_columns_are_numeric_18_4(db: Connection) -> None:
    rows = db.execute(
        text(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND data_type = 'numeric'
              AND numeric_precision = 18 AND numeric_scale = 4
            """
        )
    )

    assert {(row.table_name, row.column_name) for row in rows} == MONEY_COLUMNS


def test_redelivered_attachment_is_rejected(db: Connection) -> None:
    insert_document(db)

    with pytest.raises(IntegrityError, match="documents_gmail_message_id_sha256_key"):
        insert_document(db)


def test_same_file_in_another_message_is_allowed(db: Connection) -> None:
    insert_document(db, message_id="msg-1")
    insert_document(db, message_id="msg-2")

    count = db.execute(text("SELECT count(*) FROM documents WHERE sha256 = :sha"), {"sha": SHA_A})
    assert count.scalar_one() == 2


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "done"),
        ("skip_reason", "spam"),
        ("failure_reason", "unknown"),
        ("extraction_path", "ocr"),
        ("attempts", -1),
    ],
)
def test_document_check_constraints(db: Connection, column: str, value: str | int) -> None:
    document_id = insert_document(db)

    with pytest.raises(IntegrityError):
        db.execute(
            text(f"UPDATE documents SET {column} = :value WHERE id = :id"),
            {"value": value, "id": document_id},
        )


def test_sha256_must_be_lowercase_hex(db: Connection) -> None:
    with pytest.raises(IntegrityError):
        insert_document(db, sha256="A" * 64)


def test_duplicate_invoice_number_per_vendor_is_rejected(db: Connection) -> None:
    vendor_id = insert_vendor(db)
    insert_invoice(db, insert_document(db, message_id="msg-1"), vendor_id)

    with pytest.raises(IntegrityError, match="invoices_vendor_id_invoice_number_normalized_key"):
        insert_invoice(db, insert_document(db, message_id="msg-2"), vendor_id)


def test_same_invoice_number_for_another_vendor_is_allowed(db: Connection) -> None:
    insert_invoice(db, insert_document(db, message_id="msg-1"), insert_vendor(db, "acme"))
    insert_invoice(db, insert_document(db, message_id="msg-2"), insert_vendor(db, "globex"))

    assert db.execute(text("SELECT count(*) FROM invoices")).scalar_one() == 2


def test_currency_must_be_iso_code(db: Connection) -> None:
    with pytest.raises(IntegrityError):
        insert_invoice(db, insert_document(db), insert_vendor(db), currency="$")


def test_document_with_history_cannot_be_deleted(db: Connection) -> None:
    document_id = insert_document(db)
    db.execute(
        text(
            """
            INSERT INTO document_events (document_id, event_type, actor)
            VALUES (:id, 'received', 'system')
            """
        ),
        {"id": document_id},
    )

    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})


def test_updated_at_is_refreshed_on_update(db: Connection) -> None:
    document_id = insert_document(db)
    db.execute(
        text("UPDATE documents SET updated_at = TIMESTAMPTZ '2000-01-01' WHERE id = :id"),
        {"id": document_id},
    )

    updated_at = db.execute(
        text("SELECT updated_at > TIMESTAMPTZ '2000-01-01' FROM documents WHERE id = :id"),
        {"id": document_id},
    )
    assert updated_at.scalar_one() is True
