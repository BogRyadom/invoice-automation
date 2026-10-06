# Vendor matching (docs/SPEC.md section 8): tax id, then exact normalized name or alias,
# then fuzzy candidates that are only suggestions for the reviewer, never merged automatically.

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rapidfuzz import fuzz, process

from app.extraction.normalize import DateOrder, compact_key, vendor_key

MAX_CANDIDATES = 3


@dataclass(frozen=True)
class VendorRecord:
    id: UUID
    canonical_name: str
    normalized_name: str
    tax_id: str | None
    date_format: DateOrder | None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    vendor_id: UUID
    canonical_name: str
    score: int


@dataclass(frozen=True)
class VendorMatch:
    vendor: VendorRecord | None
    method: Literal["tax_id", "name", "alias", "none"]
    candidates: tuple[Candidate, ...] = ()

    @property
    def is_exact(self) -> bool:
        """True when the vendor was identified without fuzzy matching."""
        return self.vendor is not None


def match_vendor(
    name_raw: str | None,
    tax_id_raw: str | None,
    vendors: Sequence[VendorRecord],
    fuzzy_threshold: int,
) -> VendorMatch:
    """Identify the vendor of an invoice from its printed name and tax id."""
    if tax_id_raw:
        tax_id = compact_key(tax_id_raw)
        for vendor in vendors:
            if vendor.tax_id and vendor.tax_id == tax_id:
                return VendorMatch(vendor, "tax_id")
    if not name_raw:
        return VendorMatch(None, "none")

    key = vendor_key(name_raw)
    for vendor in vendors:
        if vendor.normalized_name == key:
            return VendorMatch(vendor, "name")
    for vendor in vendors:
        if key in vendor.aliases:
            return VendorMatch(vendor, "alias")

    names = {
        (vendor.id, name): name
        for vendor in vendors
        for name in (vendor.normalized_name, *vendor.aliases)
    }
    by_id = {vendor.id: vendor for vendor in vendors}
    best: dict[UUID, int] = {}
    for _, score, (vendor_id, _) in process.extract(
        key, names, scorer=fuzz.token_sort_ratio, score_cutoff=fuzzy_threshold, limit=None
    ):
        best[vendor_id] = max(best.get(vendor_id, 0), round(score))
    ranked = sorted(best.items(), key=lambda item: (-item[1], by_id[item[0]].canonical_name))
    candidates = tuple(
        Candidate(vendor_id, by_id[vendor_id].canonical_name, score)
        for vendor_id, score in ranked[:MAX_CANDIDATES]
    )
    return VendorMatch(None, "none", candidates)
