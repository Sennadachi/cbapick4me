"""Non-interactive mode: everything from flags, prints a summary, writes the CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.bom import BomError, guess_columns, read_table
from .core.formats import DELIMITERS, ROLE_NAMES, CsvFormat, FormatStore, load_formats
from .core.padding import ALREADY_MET, UNREACHABLE
from .core.parse import Kind, format_value
from .core.picker import total_cost, unique_keys
from .core.session import Session
from .core.specs import Spec, coerce_field, parse_override
from .core.suppliers import SupplierError


def add_spec_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("specs")
    g.add_argument("--boards", type=int, help="number of boards to build (default 1)")
    g.add_argument("--dielectric", help="capacitor dielectric: X7R, X5R, C0G … (default X7R)")
    g.add_argument("--cap-voltage", help="minimum capacitor rated voltage, e.g. 25 (default 16)")
    g.add_argument("--cap-tolerance", help="maximum capacitor tolerance in %%, or 'any' (default any)")
    g.add_argument("--res-power", help="minimum resistor power: 0.1, 1/10W, 100mW (default 0.1)")
    g.add_argument("--res-tolerance", help="maximum resistor tolerance in %%, or 'any' (default 1)")
    g.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="DES:field=val",
        help="per-designator exception, e.g. C9:dielectric=X5R or R4,R5:value=49.9 (repeatable)",
    )
    g.add_argument("--cheap-threshold", type=float, help="unit price below which spares are added (default 0.10)")
    g.add_argument("--spare-budget", type=float, help="max extra spend per line for spares (default 2.00)")
    g.add_argument("--stock-factor", type=float, help="require stock ≥ needed × this (default 10)")
    g.add_argument(
        "--pad-to",
        type=float,
        metavar="AMOUNT",
        help="basket padding: price the other BOM lines by MPN and aim for this basket total (e.g. free-shipping threshold)",
    )
    g.add_argument(
        "--pad",
        action="append",
        default=[],
        metavar="DES,DES",
        help="headless: lines to increase to reach --pad-to, by any of their designators (repeatable)",
    )


def add_format_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("BOM format (default: EasyEDA, columns recognised by name)")
    g.add_argument(
        "--format",
        choices=["easyeda", "custom"],
        help="custom = map your EDA tool's CSV headers yourself (default: whatever you used last)",
    )
    g.add_argument("--preset", metavar="NAME", help="use a saved custom CSV preset (implies --format custom)")
    g.add_argument(
        "--col",
        action="append",
        default=[],
        metavar="ROLE=HEADER",
        help=f"custom CSV column, e.g. designator=Reference (repeatable; implies --format custom). Roles: {', '.join(ROLE_NAMES)}",
    )
    g.add_argument("--delimiter", choices=list(DELIMITERS), help="custom CSV delimiter (default auto)")
    g.add_argument("--header-row", type=int, metavar="N", help="custom CSV: line number of the header row (default 1)")


def wants_custom(a: argparse.Namespace, store: FormatStore) -> bool:
    """Whether this run starts in custom CSV mode: flags first, then the remembered toggle."""
    if a.preset or a.col:
        return True
    if a.format:
        return a.format == "custom"
    return store.custom


def format_from_args(a: argparse.Namespace, store: FormatStore, bom: Path | None = None) -> CsvFormat | None:
    """The CsvFormat the flags ask for, or None for EasyEDA / for "let the UI ask".

    With custom mode on but no --col/--preset, headless uses the last mapping, or
    guesses from the file's headers if there is none. The interactive UIs get None
    then (when no flag pins the format) and show their mapping form instead.
    """
    if not wants_custom(a, store):
        return None
    if a.preset:
        if a.preset not in store.presets:
            known = ", ".join(store.presets) or "none saved yet"
            raise ValueError(f"No preset called '{a.preset}' (presets: {known})")
        fmt = store.presets[a.preset]
        fmt = CsvFormat(dict(fmt.columns), fmt.delimiter, fmt.header_row)
    elif a.col:
        cols = {}
        for text in a.col:
            role, sep, header = text.partition("=")
            role = role.strip().lower()
            if not sep or role not in ROLE_NAMES:
                raise ValueError(f"--col {text!r}: expected ROLE=HEADER with ROLE one of {', '.join(ROLE_NAMES)}")
            cols[role] = header.strip()
        fmt = CsvFormat(cols)
    elif not a.headless:
        return None
    elif store.last:
        fmt = CsvFormat(dict(store.last.columns), store.last.delimiter, store.last.header_row)
    else:
        fmt = CsvFormat()
    if a.delimiter:
        fmt.delimiter = DELIMITERS[a.delimiter]
    if a.header_row:
        fmt.header_row = a.header_row
    if not fmt.columns and bom is not None:
        headers, _ = read_table(Path(bom).read_bytes(), fmt.delimiter, fmt.header_row)
        fmt.columns = guess_columns(headers)
    return fmt


def apply_spec_args(session: Session, a: argparse.Namespace) -> None:
    s = session.settings
    if a.boards:
        s.boards = a.boards
    cap = {}
    if a.dielectric is not None:
        cap["dielectric"] = coerce_field("dielectric", a.dielectric)
    if a.cap_voltage is not None:
        cap["min_voltage"] = coerce_field("min_voltage", a.cap_voltage)
    if a.cap_tolerance is not None:
        cap["tolerance"] = coerce_field("tolerance", a.cap_tolerance)
    res = {}
    if a.res_power is not None:
        res["min_power"] = coerce_field("min_power", a.res_power)
    if a.res_tolerance is not None:
        res["tolerance"] = coerce_field("tolerance", a.res_tolerance)
    s.cap = Spec(**{**s.cap.__dict__, **cap})
    s.res = Spec(**{**s.res.__dict__, **res})
    for attr in ("cheap_threshold", "spare_budget", "stock_factor"):
        v = getattr(a, attr)
        if v is not None:
            setattr(s, attr, v)
    if a.pad_to is not None:
        if a.pad_to <= 0:
            raise ValueError("--pad-to must be more than 0")
        s.pad_enabled = True
        s.pad_threshold = a.pad_to


def apply_overrides(session: Session, overrides: list[str]) -> None:
    for text in overrides:
        designators, fields = parse_override(text)
        session.apply_exception(designators, fields)


def run(a: argparse.Namespace) -> int:
    if not a.bom:
        print("error: --headless needs a BOM file", file=sys.stderr)
        return 2
    session = Session()
    try:
        session.csv_format = format_from_args(a, load_formats(), Path(a.bom))
        session.load_path(a.bom)
        apply_spec_args(session, a)
        apply_overrides(session, a.override)
    except (OSError, BomError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    s = session.settings
    print(f"BOM: {session.bom.name} — {len(session.bom.pickable_rows)} C/R lines, boards: {s.boards}")
    print(f"Capacitors: {s.cap.describe(Kind.CAPACITOR)}    Resistors: {s.res.describe(Kind.RESISTOR)}")

    if a.dry_run:
        from .core.suppliers.digikey import build_queries

        for line in session.lines():
            if line.key is None:
                print(f"  {','.join(line.designators):30} SKIP: {line.problem}")
                continue
            print(f"  {','.join(line.designators):30} need {line.needed:>5}  query: {' | '.join(build_queries(line.key))!r}")
        print(f"{len(unique_keys(session.lines()))} unique searches (no network used)")
        return 0

    def progress(done: int, total: int, label: str) -> None:
        print(f"\r  searching {done}/{total} {label:<20}", end="", file=sys.stderr, flush=True)

    try:
        session.run(on_progress=progress)
        print(file=sys.stderr)
        if a.allow_nearest and session.accept_all_nearest():
            print("Substituting nearest standard values and searching again…", file=sys.stderr)
            session.run(on_progress=progress)
            print(file=sys.stderr)
    except SupplierError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1

    print_summary(session)
    if s.pad_enabled:
        pad_basket(session, a.pad)
    path = session.save(a.output)
    print(f"\nWrote {path}")
    return 0 if all(r.ok for r in session.results) else 3


def print_summary(session: Session) -> None:
    rows = []
    for r in session.results:
        ln = r.line
        p = r.pick
        rows.append(
            [
                _clip(",".join(ln.designators), 24),
                format_value(ln.value, ln.kind) if ln.value is not None else ln.row.value_text,
                ln.size or "?",
                ln.spec.describe(ln.kind),
                p.candidate.mpn if p else "— " + r.note[:60],
                p.candidate.supplier_part if p else "",
                str(ln.needed),
                str(p.order_qty) if p else "",
                f"{p.line_total:.2f}" if p else "",
                str(p.candidate.stock) if p else "",
            ]
        )
    headers = ["Designators", "Value", "Size", "Spec", "MPN", "Supplier PN", "Need", "Order", "Total", "Stock"]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print()
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    print(f"\nTotal for picked passives: {total_cost(session.results):.2f} {session.currency}")
    missing = [r for r in session.results if not r.ok]
    if missing:
        print(f"{len(missing)} line(s) not picked:")
        for r in missing:
            print(f"  {','.join(r.line.designators)}: {r.note}")


def pad_basket(session: Session, pad_args: list[str]) -> None:
    cur = session.currency
    threshold = session.settings.pad_threshold
    priced = [x for x in session.extras if x.pick]
    print(f"\nOther lines priced by MPN: {len(priced)} of {len(session.extras)}")
    for x in session.extras:
        if not x.pick:
            print(f"  {','.join(x.designators)} ({x.query}): {x.note}")
    print(f"Basket: {session.total:.2f} {cur} / threshold {threshold:.2f} {cur}")
    if not pad_args:
        if session.shortfall:
            print(f"{session.shortfall:.2f} {cur} short — choose lines to increase with --pad R1,C5,U2")
        return

    wanted = {d.strip() for arg in pad_args for d in arg.split(",") if d.strip()}
    basket = session.basket()
    indices = [i for i, x in enumerate(basket) if wanted & set(x.designators)]
    unknown = wanted - {d for i in indices for d in basket[i].designators}
    if unknown:
        print(f"warning: --pad designators not in the basket: {', '.join(sorted(unknown))}", file=sys.stderr)
    outcome = session.pad(indices)
    if outcome.status == ALREADY_MET:
        print("Threshold already met — nothing padded")
        return
    for i in indices:
        x = basket[i]
        if x.pad:
            extra = x.pad.line_total - x.base_pick.line_total
            print(f"  {_clip(','.join(x.designators), 24):24} +{x.pad.padded:<6} → order {x.pad.order_qty:<6} +{extra:.2f} {cur}")
        elif not x.base_pick:
            print(f"  {_clip(','.join(x.designators), 24):24} not priced — can't pad")
    print(f"Padded by {outcome.added:.2f} {cur} → basket {outcome.total:.2f} {cur}")
    if outcome.status == UNREACHABLE:
        print(f"warning: stock limits stop these lines reaching {threshold:.2f} {cur} — add more lines", file=sys.stderr)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
