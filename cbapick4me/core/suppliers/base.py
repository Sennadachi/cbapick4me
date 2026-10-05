"""Supplier interface (implemented by DigiKey; tests use a fake)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..specs import SearchKey


class SupplierError(RuntimeError):
    pass


class SupplierAuthError(SupplierError):
    """Credentials rejected — retrying other searches is pointless."""


@dataclass
class Candidate:
    mpn: str
    manufacturer: str
    supplier: str
    supplier_part: str
    description: str
    stock: int
    price_breaks: list[tuple[int, float]]  # (break qty, unit price), ascending qty
    moq: int = 1
    packaging: str = ""
    url: str = ""
    currency: str = ""
    # Parsed attributes used for filtering (value, size, dielectric, voltage, power, tolerance).
    attrs: dict[str, object] = field(default_factory=dict)


class Supplier(Protocol):
    name: str  # display name written to the BOM "Supplier" column

    def search(self, key: SearchKey) -> list[Candidate]:
        """Return in-stock candidates for this spec. Filtering by spec is done by the caller too."""
        ...

    def describe_query(self, key: SearchKey) -> str:
        """The keyword query that would be sent (used for --dry-run)."""
        ...

    def check(self) -> str:
        """Verify the credentials with one cheap request; return a success message or raise."""
        ...

    def lookup(self, mpn: str, supplier_part: str = "") -> list[Candidate]:
        """In-stock offers for exactly this manufacturer part (supplier_part is tried first if given)."""
        ...


def _norm_pn(text: str) -> str:
    return "".join(text.split()).upper()


def exact_mpn(cands: list[Candidate], mpn: str, supplier_part: str = "") -> list[Candidate]:
    """Candidates whose MPN equals mpn (ignoring case and spaces), or that share an MPN with supplier_part."""
    wanted = {_norm_pn(mpn)}
    if supplier_part:
        wanted |= {_norm_pn(c.mpn) for c in cands if _norm_pn(c.supplier_part) == _norm_pn(supplier_part)}
    return [c for c in cands if _norm_pn(c.mpn) in wanted]


# fetch(query, page) -> (candidates on that page, raw results on page, total results)
PageFetcher = Callable[[str, int], tuple[list[Candidate], int, int]]


def search_queries(queries: list[str], fetch: PageFetcher, key: SearchKey, *, page_size: int, max_pages: int) -> list[Candidate]:
    """Try each query spelling in turn, merging results, until enough parts match the spec."""
    from ..match import filter_candidates

    found: dict[str, Candidate] = {}
    for query in queries:
        for page in range(max_pages):
            cands, n_raw, total = fetch(query, page)
            for c in cands:
                found.setdefault(c.supplier_part or f"{c.mpn}|{c.packaging}", c)
            if len(filter_candidates(list(found.values()), key)) >= 5:
                return list(found.values())
            if not n_raw or (page + 1) * page_size >= total:
                break
    return list(found.values())
