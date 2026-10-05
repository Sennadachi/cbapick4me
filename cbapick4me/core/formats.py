"""Custom CSV column mappings: which header holds the designator, value, footprint…

EasyEDA BOMs need none of this (columns are recognised by name). For other EDA
tools the user maps headers to roles once; the last mapping and any named
presets are kept in bom_formats.toml next to config.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

from .bom import guess_columns
from .config import APP

# (role, label, required) in the order the mapping forms show them.
ROLES: list[tuple[str, str, bool]] = [
    ("designator", "Designator", True),
    ("value", "Value", True),
    ("footprint", "Footprint / package", False),
    ("quantity", "Quantity", False),
    ("mpn", "Manufacturer part", False),
    ("manufacturer", "Manufacturer", False),
    ("supplier_part", "Supplier part", False),
    ("supplier", "Supplier", False),
    ("price", "Price", False),
    ("id", "Line no.", False),
]
ROLE_NAMES = [r for r, _, _ in ROLES]

# Name used in forms and on the command line -> delimiter character ("" = detect).
DELIMITERS = {"auto": "", "comma": ",", "tab": "\t", "semicolon": ";"}


@dataclass
class CsvFormat:
    columns: dict[str, str] = field(default_factory=dict)  # role -> header ("" = not used)
    delimiter: str = ""  # "" = detect
    header_row: int = 1  # 1-based line holding the headers

    def fits(self, headers: list[str]) -> bool:
        """True if every header this mapping uses exists in a file with these headers."""
        used = [h for h in self.columns.values() if h]
        return bool(used) and all(h in headers for h in used)


@dataclass
class FormatStore:
    custom: bool = False  # the "Custom CSV" toggle
    last: CsvFormat | None = None
    presets: dict[str, CsvFormat] = field(default_factory=dict)


def formats_path() -> Path:
    return Path(user_config_dir(APP, appauthor=False)) / "bom_formats.toml"


def _from_table(t: dict) -> CsvFormat:
    cols = t.get("columns", {})
    return CsvFormat(
        columns={r: str(cols[r]) for r in ROLE_NAMES if cols.get(r)},
        delimiter=str(t.get("delimiter", "")),
        header_row=max(1, int(t.get("header_row", 1))),
    )


def load_formats() -> FormatStore:
    try:
        data = tomllib.loads(formats_path().read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return FormatStore()
    store = FormatStore(custom=bool(data.get("custom", False)))
    try:
        if "last" in data:
            store.last = _from_table(data["last"])
        for name, t in data.get("presets", {}).items():
            store.presets[name] = _from_table(t)
    except (TypeError, ValueError, AttributeError):
        pass
    return store


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n") + '"'


def _table(key: str, f: CsvFormat) -> list[str]:
    lines = [f"[{key}]", f"delimiter = {_q(f.delimiter)}", f"header_row = {f.header_row}", "", f"[{key}.columns]"]
    lines += [f"{role} = {_q(f.columns[role])}" for role in ROLE_NAMES if f.columns.get(role)]
    return lines + [""]


def save_formats(store: FormatStore) -> Path:
    lines = [f"custom = {'true' if store.custom else 'false'}", ""]
    if store.last:
        lines += _table("last", store.last)
    for name, f in store.presets.items():
        lines += _table(f"presets.{_q(name)}", f)
    path = formats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), "utf-8")
    return path


def prefill(headers: list[str], store: FormatStore, base: CsvFormat | None = None) -> CsvFormat:
    """Starting mapping for a file: the last one if it fits these headers, else a guess by name."""
    base = base or store.last or CsvFormat()
    if store.last and store.last.fits(headers):
        return CsvFormat(dict(store.last.columns), base.delimiter, base.header_row)
    return CsvFormat(guess_columns(headers), base.delimiter, base.header_row)
