from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.checks import (
    CheckContext,
    CheckResult,
    DuplicateHit,
    failed_checks,
    route,
    run_checks,
)
from app.extraction.normalize import normalize_extraction
from app.extraction.pdf import extract_text
from app.vendors import Candidate, VendorMatch, VendorRecord
from corpus.models import GroundTruth
from corpus.storage import document_path, load_corpus, load_known_vendors

DOCS = {doc.doc_id: doc for doc in load_corpus()}
KNOWN = {vendor.normalized_name for vendor in load_known_vendors()}


def context(doc: GroundTruth) -> CheckContext:
    """Checks context for a corpus document read perfectly by the model."""
    assert doc.printed is not None and doc.expected is not None
    invoice = normalize_extraction(doc.printed)
    known = doc.expected.vendor_key in KNOWN
    vendor = (
        VendorMatch(
            VendorRecord(
                uuid4(), doc.printed.vendor_name_raw or "", doc.expected.vendor_key, None, None
            ),
            "name",
        )
        if known
        else VendorMatch(None, "none")
    )
    path = doc.extraction_path or "text"
    layer = extract_text(document_path(doc).read_bytes())
    text = " ".join(layer.pages) if path == "text" else None
    duplicate = (
        DuplicateHit(uuid4(), uuid4(), doc.printed.invoice_number_raw or "", "auto_approved")
        if doc.route.duplicate_of
        else None
    )
    return CheckContext(
        extraction=doc.printed,
        invoice=invoice,
        path=path,
        text=text,
        received_on=doc.email.received_at.date(),
        vendor=vendor,
        duplicate=duplicate,
        possible_duplicate=None,
        amount_tolerance=Decimal("0.02"),
        auto_approve_max_total=Decimal("5000"),
        hidden_chars=layer.hidden_chars,
    )


def statuses(results: list[CheckResult], check_id: str) -> set[str]:
    return {r.status for r in results if r.check_id == check_id}


@pytest.mark.parametrize(
    "doc", [d for d in DOCS.values() if d.expected is not None], ids=lambda d: d.doc_id
)
def test_checks_reproduce_the_expected_route(doc: GroundTruth) -> None:
    results = run_checks(context(doc))

    assert failed_checks(results) == set(doc.route.flags)
    assert route(results, auto_approve_enabled=True) == doc.route.status


def test_every_check_reports_a_result() -> None:
    results = run_checks(context(DOCS["clean_01"]))

    ids = {r.check_id for r in results}
    assert ids == {f"H{n}" for n in range(1, 7)} | {f"W{n}" for n in range(1, 10)}
    assert all(r.severity == ("hard" if r.check_id[0] == "H" else "warning") for r in results)


def test_auto_approve_is_off_by_default_setting() -> None:
    results = run_checks(context(DOCS["clean_01"]))

    assert route(results, auto_approve_enabled=False) == "needs_review"


def test_wrong_total_fails_h2() -> None:
    ctx = context(DOCS["clean_01"])
    ctx = replace(ctx, invoice=ctx.invoice.model_copy(update={"total": Decimal("500.00")}))

    results = run_checks(ctx)

    assert statuses(results, "H2") == {"fail"}
    assert route(results, auto_approve_enabled=True) == "needs_review"


def test_total_within_tolerance_passes_h2() -> None:
    ctx = context(DOCS["clean_01"])
    total = ctx.invoice.total
    assert total is not None
    ctx = replace(ctx, invoice=ctx.invoice.model_copy(update={"total": total + Decimal("0.02")}))

    assert statuses(run_checks(ctx), "H2") == {"pass"}


def test_line_items_that_do_not_add_up_fail_h3() -> None:
    ctx = context(DOCS["clean_01"])
    items = ctx.invoice.line_items[:-1]
    ctx = replace(ctx, invoice=ctx.invoice.model_copy(update={"line_items": items}))

    assert statuses(run_checks(ctx), "H3") == {"fail"}


def test_value_missing_from_text_fails_h4() -> None:
    ctx = context(DOCS["clean_01"])
    ctx = replace(ctx, extraction=ctx.extraction.model_copy(update={"total_raw": "999.99"}))

    failures = [r for r in run_checks(ctx) if r.check_id == "H4" and r.status == "fail"]

    assert [r.field for r in failures] == ["total"]


def test_injected_values_are_caught_once_hidden_text_is_removed() -> None:
    doc = DOCS["injection_01"]
    ctx = context(doc)
    injected = ctx.extraction.model_copy(
        update={"invoice_number_raw": "PA-9999", "total_raw": "0.01"}
    )
    ctx = replace(ctx, extraction=injected, invoice=normalize_extraction(injected))

    results = run_checks(ctx)

    grounding = {r.field for r in results if r.check_id == "H4" and r.status == "fail"}
    assert grounding == {"invoice_number", "total"}
    assert statuses(results, "H2") == {"fail"}
    assert statuses(results, "W9") == {"fail"}


def test_future_invoice_date_fails_h6() -> None:
    ctx = context(DOCS["clean_01"])
    ctx = replace(ctx, received_on=date(2026, 3, 1))

    assert statuses(run_checks(ctx), "H6") == {"fail"}


def test_due_date_before_invoice_date_fails_h6() -> None:
    ctx = context(DOCS["clean_01"])
    ctx = replace(ctx, invoice=ctx.invoice.model_copy(update={"due_date": date(2026, 1, 1)}))

    failures = [r for r in run_checks(ctx) if r.check_id == "H6" and r.status == "fail"]

    assert [r.field for r in failures] == ["due_date"]


def test_missing_required_fields_fail_h1() -> None:
    ctx = context(DOCS["clean_01"])
    ctx = replace(
        ctx,
        invoice=ctx.invoice.model_copy(
            update={"invoice_number_normalized": None, "currency": None}
        ),
    )

    failures = {r.field for r in run_checks(ctx) if r.check_id == "H1" and r.status == "fail"}

    assert failures == {"invoice_number", "currency"}


def test_unparseable_value_fails_h1() -> None:
    doc = DOCS["clean_01"]
    assert doc.printed is not None
    broken = doc.printed.model_copy(update={"shipping_raw": "free"})
    ctx = replace(context(doc), extraction=broken, invoice=normalize_extraction(broken))

    failures = {r.field for r in run_checks(ctx) if r.check_id == "H1" and r.status == "fail"}

    assert failures == {"shipping"}


def test_fuzzy_candidates_are_listed_in_w4() -> None:
    candidate = Candidate(uuid4(), "Quorrin Office Supply Inc.", 91)
    ctx = replace(context(DOCS["clean_01"]), vendor=VendorMatch(None, "none", (candidate,)))

    [w4] = [r for r in run_checks(ctx) if r.check_id == "W4"]

    assert w4.status == "fail"
    assert "Quorrin Office Supply Inc. (91)" in w4.message


def test_possible_duplicate_raises_w8() -> None:
    hit = DuplicateHit(uuid4(), uuid4(), "QOS-26-0412", "approved")
    ctx = replace(context(DOCS["clean_09"]), possible_duplicate=hit)

    assert statuses(run_checks(ctx), "W8") == {"fail"}


def test_hard_check_not_run_blocks_auto_approval() -> None:
    results = [
        CheckResult("H1", "hard", "pass", None, "ok"),
        CheckResult("H4", "hard", "not_run", None, "no text"),
    ]

    assert route(results, auto_approve_enabled=True) == "needs_review"
