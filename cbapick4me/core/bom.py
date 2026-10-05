"""Reading and writing BOM CSV files.

EasyEDA Standard exports UTF-16LE, tab-separated, fully quoted. EasyEDA Pro
exports UTF-8 (often with a BOM), comma-separated. Both are recognised by
header name automatically; any other tool's CSV can be read with a CsvFormat
that says which header holds what. Everything works on bytes so the web front
end can parse uploads in memory.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .parse import Kind, classify, parse_footprint, parse_value, split_designators

if TYPE_CHECKING:
    from .formats import CsvFormat

# Role -> accepted header names (lower-case, first match wins).
COLUMN_ALIASES: dict[str, list[str]] = {
    "id": ["id", "no.", "no", "#", "item", "index"],
    "designator": ["designator", "designators", "reference", "references", "ref", "refdes"],
    "value": ["value", "comment", "name", "val"],
    "footprint": ["footprint", "package", "footprint name"],
    "quantity": ["quantity", "qty"],
    "mpn": ["manufacturer part", "manufacturer part number", "mpn", "mfr part", "mfr. part #"],
    "manufacturer": ["manufacturer", "mfr", "mfr."],
    "supplier": ["supplier"],
    "supplier_part": ["supplier part", "supplier part number", "supplier pn"],
    "price": ["price", "unit price"],
}

EXTRA_COLUMNS = ["Qty Needed", "Order Qty", "Unit Price", "Line Total", "Stock", "Pick Notes"]


class BomError(ValueError):
    pass


@dataclass
class BomRow:
    index: int
    fields: dict[str, str]
    designators: list[str]
    kind: Kind
    value_text: str
    footprint: str
    value: float | None = None
    size: str | None = None
    qty_per_board: int = 0

    @property
    def pickable(self) -> bool:
        return self.kind is not Kind.OTHER


@dataclass
class Bom:
    name: str
    headers: list[str]
    columns: dict[str, str]
    rows: list[BomRow] = field(default_factory=list)

    @property
    def pickable_rows(self) -> list[BomRow]:
        return [r for r in self.rows if r.pickable]

    @property
    def stem(self) -> str:
        return Path(self.name).stem

    def output_name(self) -> str:
        return f"{self.stem}_picked4u.csv"


def decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    # UTF-16 without BOM: lots of NUL bytes in alternating positions.
    if len(data) >= 4 and data[1:2] == b"\x00" and data[3:4] == b"\x00":
        return data.decode("utf-16-le")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _sniff_delimiter(first: str) -> str:
    try:
        return csv.Sniffer().sniff(first, delimiters=",\t;").delimiter
    except csv.Error:
        return "\t" if "\t" in first else ","


def guess_columns(headers: list[str]) -> dict[str, str]:
    lowered = {h.strip().lower(): h for h in headers}
    cols: dict[str, str] = {}
    for role, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered and lowered[alias] not in cols.values():
                cols[role] = lowered[alias]
                break
    return cols


def read_table(data: bytes, delimiter: str = "", header_row: int = 1) -> tuple[list[str], list[list[str]]]:
    """Decode a CSV and return (headers, data rows). header_row is 1-based; blank delimiter = sniff."""
    text = decode(data)
    if not text.strip():
        raise BomError("BOM file is empty")
    lines = text.split("\n")  # not splitlines(): that also splits on rarer separators inside fields
    if header_row < 1 or header_row > len(lines):
        raise BomError(f"Header row {header_row} is past the end of the file ({len(lines)} lines)")
    body = "\n".join(lines[header_row - 1 :])
    delimiter = delimiter or _sniff_delimiter(lines[header_row - 1].rstrip("\r"))
    reader = csv.reader(io.StringIO(body, newline=""), delimiter=delimiter)
    table = [r for r in reader if any(c.strip() for c in r)]
    if not table:
        raise BomError(f"No header found at row {header_row}")
    return [h.strip() for h in table[0]], table[1:]


def parse_bom(data: bytes, name: str, fmt: CsvFormat | None = None) -> Bom:
    """Parse a BOM. fmt=None recognises columns by header name (EasyEDA and similar)."""
    if fmt is None:
        headers, body = read_table(data)
        cols = guess_columns(headers)
        for required in ("designator", "value"):
            if required not in cols:
                raise BomError(
                    f"Could not find a {required} column in headers: {headers}. "
                    "If this isn't an EasyEDA BOM, switch on Custom CSV and choose the columns."
                )
    else:
        headers, body = read_table(data, fmt.delimiter, fmt.header_row)
        cols = {role: h for role, h in fmt.columns.items() if h}
        for role, h in cols.items():
            if h not in headers:
                raise BomError(f"Column '{h}' ({role}) is not in this file. Headers found: {headers}")
        for required in ("designator", "value"):
            if required not in cols:
                raise BomError(f"Choose which column holds the {required}")

    bom = Bom(name=name, headers=headers, columns=cols)
    for i, raw in enumerate(body):
        raw = raw + [""] * (len(headers) - len(raw))
        fields = dict(zip(headers, raw))
        designators = split_designators(fields[cols["designator"]])
        kinds = {classify(d) for d in designators}
        kind = kinds.pop() if len(kinds) == 1 else Kind.OTHER
        value_text = fields[cols["value"]].strip()
        footprint = fields.get(cols.get("footprint", ""), "").strip()
        qty_text = fields.get(cols.get("quantity", ""), "").strip()
        try:
            qty = int(float(qty_text)) if qty_text else len(designators)
        except ValueError:
            qty = len(designators)
        row = BomRow(
            index=i,
            fields=fields,
            designators=designators,
            kind=kind,
            value_text=value_text,
            footprint=footprint,
            qty_per_board=qty,
        )
        if row.pickable:
            row.value = parse_value(value_text, kind)
            row.size = parse_footprint(footprint)
        bom.rows.append(row)
    return bom


def load_bom(path: str | Path, fmt: CsvFormat | None = None) -> Bom:
    p = Path(path)
    return parse_bom(p.read_bytes(), p.name, fmt)


def render_csv(headers: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    # UTF-8 with BOM so Excel on Windows shows the CJK manufacturer names correctly.
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
