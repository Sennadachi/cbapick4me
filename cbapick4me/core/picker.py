"""Orchestration: search each line, rank candidates, render the output CSV."""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .bom import EXTRA_COLUMNS, Bom, BomRow, render_csv
from .match import filter_candidates, mismatch
from .parse import Kind, format_value, is_standard_resistance, nearest_standard
from .pricing import Pick, rank
from .specs import Line, SearchKey, Settings, build_lines
from .suppliers.base import Candidate, Supplier, SupplierAuthError, SupplierError

TOP_N = 5

ProgressFn = Callable[[int, int, str], None]


@dataclass(kw_only=True)
class Priced:
    """A basket line: ranked picks, the chosen one, and any basket padding on top."""

    picks: list[Pick] = field(default_factory=list)
    selected: int | None = None
    note: str = ""
    # The selected pick with its quantity raised by basket padding.
    pad: Pick | None = None

    @property
    def base_pick(self) -> Pick | None:
        if self.selected is None or not self.picks:
            return None
        return self.picks[self.selected]

    @property
    def pick(self) -> Pick | None:
        return self.pad or self.base_pick

    @property
    def ok(self) -> bool:
        return self.pick is not None


@dataclass
class LineResult(Priced):
    line: Line
    # Nearest standard values to offer when the BOM value has no exact match.
    suggestions: list[float] = field(default_factory=list)

    @property
    def row(self) -> BomRow:
        return self.line.row

    @property
    def designators(self) -> list[str]:
        return self.line.designators

    @property
    def needed(self) -> int:
        return self.line.needed


@dataclass
class ExtraResult(Priced):
    """A non-C/R BOM row priced at the supplier by its manufacturer part number."""

    row: BomRow
    needed: int
    query: str

    @property
    def designators(self) -> list[str]:
        return self.row.designators


class Cancelled(Exception):
    pass


def unique_keys(lines: list[Line]) -> list[SearchKey]:
    seen: dict[SearchKey, None] = {}
    for ln in lines:
        if ln.key is not None:
            seen.setdefault(ln.key)
    return list(seen)


def run_searches(
    supplier: Supplier,
    keys: list[SearchKey],
    *,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    workers: int = 4,
) -> dict[SearchKey, list[Candidate] | SupplierError]:
    results: dict[SearchKey, list[Candidate] | SupplierError] = {}
    total = len(keys)
    if on_progress:
        on_progress(0, total, "Searching…")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(supplier.search, k): k for k in keys}
        done = 0
        for fut in as_completed(futures):
            k = futures[fut]
            if cancel and cancel.is_set():
                for f in futures:
                    f.cancel()
                raise Cancelled()
            try:
                results[k] = fut.result()
            except SupplierAuthError:
                # Bad keys fail every search the same way: stop and say so once.
                for f in futures:
                    f.cancel()
                raise
            except SupplierError as e:
                results[k] = e
            done += 1
            if on_progress:
                on_progress(done, total, f"{format_value(k.value, k.kind)} {k.size}")
    return results


def pick(
    bom: Bom,
    settings: Settings,
    supplier: Supplier,
    *,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> list[LineResult]:
    lines = build_lines(bom, settings)
    found = run_searches(supplier, unique_keys(lines), on_progress=on_progress, cancel=cancel)
    return [evaluate(ln, found.get(ln.key) if ln.key else None, settings) for ln in lines]


def evaluate(line: Line, found: list[Candidate] | SupplierError | None, settings: Settings) -> LineResult:
    res = LineResult(line)
    if line.problem:
        res.note = line.problem
        return res
    if isinstance(found, SupplierError):
        res.note = f"Search failed: {found}"
        return res
    found = found or []
    key = line.key
    matching = filter_candidates(found, key)
    picks, note = rank(
        matching,
        line.needed,
        stock_factor=settings.stock_factor,
        cheap_threshold=settings.cheap_threshold,
        spare_budget=settings.spare_budget,
    )
    res.picks = _dedupe(picks)[:TOP_N]
    res.selected = 0 if res.picks else None
    if note:
        res.note = note
    if not res.picks:
        res.note = _explain_no_match(found, key, line.needed)
        if line.kind is Kind.RESISTOR and line.value and not is_standard_resistance(line.value):
            res.suggestions = nearest_standard(line.value)
            alts = ", ".join(format_value(v, line.kind) for v in res.suggestions)
            res.note += f". {format_value(line.value, line.kind)} is not a standard value — try {alts}"
    return res


def lookup_others(
    bom: Bom,
    settings: Settings,
    supplier: Supplier,
    *,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    workers: int = 4,
) -> list[ExtraResult]:
    """Price every non-C/R row by MPN (or its value text when the MPN is blank)."""
    c_mpn, c_sup, c_spn = bom.columns.get("mpn"), bom.columns.get("supplier"), bom.columns.get("supplier_part")
    extras = []
    for row in bom.rows:
        if row.pickable or row.qty_per_board <= 0:
            continue
        query = (row.fields.get(c_mpn, "") if c_mpn else "").strip() or row.value_text
        if not query:
            continue
        extras.append(ExtraResult(row=row, needed=row.qty_per_board * settings.boards, query=query))

    def hint(row: BomRow) -> str:
        # Reuse the BOM's supplier part number when it's for this supplier (e.g. "Digi-Key").
        named = _norm(row.fields.get(c_sup, "")) if c_sup else ""
        return row.fields.get(c_spn, "").strip() if c_spn and named and named == _norm(supplier.name) else ""

    total = len(extras)
    if on_progress:
        on_progress(0, total, "Pricing other parts…")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(supplier.lookup, x.query, hint(x.row)): x for x in extras}
        done = 0
        for fut in as_completed(futures):
            x = futures[fut]
            if cancel and cancel.is_set():
                for f in futures:
                    f.cancel()
                raise Cancelled()
            try:
                found = fut.result()
            except SupplierAuthError:
                for f in futures:
                    f.cancel()
                raise
            except SupplierError as e:
                x.note = f"Lookup failed: {e}"
                found = None
            if found is not None:
                picks, _ = rank(found, x.needed, stock_factor=1, cheap_threshold=0, spare_budget=0)
                x.picks = _dedupe(picks)[:TOP_N]
                x.selected = 0 if x.picks else None
                if not x.picks:
                    x.note = f"Not found at {supplier.name} by MPN — kept as-is"
            done += 1
            if on_progress:
                on_progress(done, total, x.query)
    return extras


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _dedupe(picks: list[Pick]) -> list[Pick]:
    """Keep the best packaging per MPN so alternatives are distinct parts."""
    seen: set[str] = set()
    out = []
    for p in picks:
        if p.candidate.mpn in seen:
            continue
        seen.add(p.candidate.mpn)
        out.append(p)
    return out


def _explain_no_match(found: list[Candidate], key: SearchKey, needed: int) -> str:
    if not found:
        return "No in-stock results from supplier"
    reasons = Counter(mismatch(c, key) for c in found)
    if reasons.get(None):
        return f"Matches found but none with ≥{needed} in stock"
    top = ", ".join(f"{r} ({n})" for r, n in reasons.most_common(3))
    return f"No exact match among {len(found)} results — rejected on {top}"


def substitute_value(settings: Settings, line: Line, value: float) -> None:
    """Accept a nearest-standard value for every designator on the line."""
    for d in line.designators:
        settings.set_override(d, value=value)


def output_headers(bom: Bom) -> list[str]:
    headers = list(bom.headers)
    defaults = {
        "mpn": "Manufacturer Part",
        "manufacturer": "Manufacturer",
        "supplier": "Supplier",
        "supplier_part": "Supplier Part",
        "quantity": "Quantity",
    }
    for role, name in defaults.items():
        if role not in bom.columns and name not in headers:
            headers.append(name)
    return headers + [c for c in EXTRA_COLUMNS if c not in headers]


def _col(bom: Bom, role: str, fallback: str) -> str:
    return bom.columns.get(role, fallback)


def output_rows(
    bom: Bom, results: list[LineResult], settings: Settings, extras: list[ExtraResult] = ()
) -> list[dict[str, str]]:
    by_row: dict[int, list[LineResult]] = {}
    for r in results:
        by_row.setdefault(r.line.row.index, []).append(r)
    extra_by_row = {x.row.index: x for x in extras}

    c_des = _col(bom, "designator", "Designator")
    c_qty = _col(bom, "quantity", "Quantity")
    c_val = _col(bom, "value", "Value")
    c_mpn = _col(bom, "mpn", "Manufacturer Part")
    c_mfr = _col(bom, "manufacturer", "Manufacturer")
    c_sup = _col(bom, "supplier", "Supplier")
    c_spn = _col(bom, "supplier_part", "Supplier Part")
    c_price = bom.columns.get("price")

    rows: list[dict[str, str]] = []
    for row in bom.rows:
        if row.index not in by_row:
            out = dict(row.fields)
            needed = row.qty_per_board * settings.boards
            out[c_qty] = str(needed)
            out["Qty Needed"] = str(needed)
            out["Order Qty"] = str(needed)
            x = extra_by_row.get(row.index)
            if x and x.pick:
                _fill_pick(out, x.pick, (c_mpn, c_mfr, c_sup, c_spn, c_qty, c_price))
                out["Pick Notes"] = "; ".join(["Priced by MPN", *_qty_notes(x.pick)])
            elif x and x.note:
                out["Pick Notes"] = x.note
            rows.append(out)
            continue
        for r in by_row[row.index]:
            ln = r.line
            out = dict(row.fields)
            out[c_des] = ",".join(ln.designators)
            out["Qty Needed"] = str(ln.needed)
            notes = [r.note] if r.note else []
            if ln.value is not None and row.value is not None and ln.value != row.value:
                out[c_val] = format_value(ln.value, ln.kind)
                notes.append(f"Value changed from {row.value_text}")
            notes.append(f"Spec: {ln.spec.describe(ln.kind)}")
            p = r.pick
            if p:
                _fill_pick(out, p, (c_mpn, c_mfr, c_sup, c_spn, c_qty, c_price))
                notes.extend(_qty_notes(p))
            else:
                out[c_qty] = str(ln.needed)
                out["Order Qty"] = ""
                notes.insert(0, "NOT PICKED")
            out["Pick Notes"] = "; ".join(notes)
            rows.append(out)
    # Exceptions split BOM lines, so renumber the ID column to keep every row unique.
    if c_id := bom.columns.get("id"):
        for n, out in enumerate(rows, 1):
            out[c_id] = str(n)
    return rows


def _fill_pick(out: dict[str, str], p: Pick, cols: tuple) -> None:
    c_mpn, c_mfr, c_sup, c_spn, c_qty, c_price = cols
    c = p.candidate
    out[c_mpn] = c.mpn
    out[c_mfr] = c.manufacturer
    out[c_sup] = c.supplier
    out[c_spn] = c.supplier_part
    out[c_qty] = str(p.order_qty)
    out["Order Qty"] = str(p.order_qty)
    out["Unit Price"] = f"{p.unit_price:.5g}"
    out["Line Total"] = f"{p.line_total:.2f}"
    out["Stock"] = str(c.stock)
    if c_price:
        out[c_price] = f"{p.unit_price:.5g}"


def _qty_notes(p: Pick) -> list[str]:
    notes = []
    if p.spares:
        notes.append(f"+{p.spares} spares")
    if p.padded:
        notes.append(f"+{p.padded} padding")
    return notes


def render_output(bom: Bom, results: list[LineResult], settings: Settings, extras: list[ExtraResult] = ()) -> bytes:
    return render_csv(output_headers(bom), output_rows(bom, results, settings, extras))


def total_cost(items: Iterable[Priced]) -> float:
    return round(sum(r.pick.line_total for r in items if r.pick), 2)
