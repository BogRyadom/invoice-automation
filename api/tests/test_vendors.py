from uuid import uuid4

import pytest

from app.vendors import VendorRecord, match_vendor

QUORRIN = VendorRecord(
    uuid4(), "Quorrin Office Supply Inc.", "quorrin office supply", "ZZ482910376", None
)
BREVIK = VendorRecord(
    uuid4(), "Brevik Software Corp.", "brevik software", None, "MDY", ("brevik soft",)
)
VENDORS = [QUORRIN, BREVIK]


@pytest.mark.parametrize(
    ("name", "tax_id", "vendor", "method"),
    [
        ("Quorrin Office Supply Inc.", "ZZ482910376", QUORRIN, "tax_id"),
        ("Some Other Name", "ZZ 482 910 376", QUORRIN, "tax_id"),
        ("QUORRIN OFFICE SUPPLY, INC", None, QUORRIN, "name"),
        ("Brevik Software Corp.", None, BREVIK, "name"),
        ("Brevik Soft Ltd", None, BREVIK, "alias"),
    ],
)
def test_exact_matches(name: str, tax_id: str | None, vendor: VendorRecord, method: str) -> None:
    match = match_vendor(name, tax_id, VENDORS, fuzzy_threshold=85)

    assert match.vendor == vendor
    assert match.method == method
    assert match.is_exact


def test_tax_id_wins_over_a_different_name() -> None:
    match = match_vendor("Brevik Software Corp.", "ZZ482910376", VENDORS, fuzzy_threshold=85)

    assert match.vendor == QUORRIN


def test_similar_name_is_only_a_suggestion() -> None:
    match = match_vendor("Quorin Office Suply", None, VENDORS, fuzzy_threshold=85)

    assert match.vendor is None
    assert not match.is_exact
    assert [c.vendor_id for c in match.candidates] == [QUORRIN.id]
    assert match.candidates[0].score >= 85


def test_unknown_vendor_has_no_candidates() -> None:
    match = match_vendor("Pellucid Analytics Ltd", None, VENDORS, fuzzy_threshold=85)

    assert match.vendor is None
    assert match.candidates == ()


def test_missing_name_and_tax_id() -> None:
    assert match_vendor(None, None, VENDORS, fuzzy_threshold=85).method == "none"
