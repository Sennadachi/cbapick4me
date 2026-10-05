"""Blanket specs, per-designator overrides, and grouping into pick lines."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .bom import Bom, BomRow
from .parse import Kind

DIELECTRICS = ["X7R", "X5R", "C0G", "X6S", "X7S", "X8R", "Y5V"]
# Equivalent names suppliers use for C0G.
DIELECTRIC_ALIASES = {"C0G": {"C0G", "NP0", "C0G/NP0", "C0G, NP0", "NP0/C0G"}}

SPEC_FIELDS = ("dielectric", "min_voltage", "min_power", "tolerance")


@dataclass(frozen=True)
class Spec:
    """Requirements for a part. Fields not relevant to a kind are ignored.

    tolerance is a maximum, in percent (1.0 means ±1% or tighter); None = any.
    """

    dielectric: str | None = None
    min_voltage: float | None = None
    min_power: float | None = None
    tolerance: float | None = None

    def for_kind(self, kind: Kind) -> Spec:
        if kind is Kind.CAPACITOR:
            return replace(self, min_power=None)
        if kind is Kind.RESISTOR:
            return replace(self, dielectric=None, min_voltage=None)
        return Spec()

    def describe(self, kind: Kind) -> str:
        parts = []
        if kind is Kind.CAPACITOR:
            if self.dielectric:
                parts.append(self.dielectric)
            if self.min_voltage:
                parts.append(f"≥{self.min_voltage:g}V")
        elif kind is Kind.RESISTOR and self.min_power:
            parts.append(f"≥{_watts(self.min_power)}")
        if self.tolerance:
            parts.append(f"≤±{self.tolerance:g}%")
        return " ".join(parts) or "any"


def _watts(w: float) -> str:
    return f"{w * 1000:g}mW" if w < 1 else f"{w:g}W"


@dataclass
class Settings:
    boards: int = 1
    cap: Spec = field(default_factory=lambda: Spec(dielectric="X7R", min_voltage=16.0))
    res: Spec = field(default_factory=lambda: Spec(min_power=0.1, tolerance=1.0))
    # designator -> {field: value}; field may be any of SPEC_FIELDS or "value" (substitute value).
    overrides: dict[str, dict[str, object]] = field(default_factory=dict)
    cheap_threshold: float = 0.10
    spare_budget: float = 2.00
    stock_factor: float = 10.0
    # Basket padding: price the rest of the BOM by MPN and top chosen lines up to this total.
    pad_enabled: bool = False
    pad_threshold: float = 0.0

    def blanket(self, kind: Kind) -> Spec:
        return self.cap if kind is Kind.CAPACITOR else self.res

    def effective_spec(self, designator: str, kind: Kind) -> Spec:
        spec = self.blanket(kind)
        ov = {k: v for k, v in self.overrides.get(designator, {}).items() if k in SPEC_FIELDS}
        return replace(spec, **ov).for_kind(kind) if ov else spec.for_kind(kind)

    def effective_value(self, designator: str, row: BomRow) -> float | None:
        v = self.overrides.get(designator, {}).get("value")
        return float(v) if v is not None else row.value

    def set_override(self, designator: str, **fields: object) -> None:
        cur = self.overrides.setdefault(designator, {})
        for k, v in fields.items():
            if k not in SPEC_FIELDS and k != "value":
                raise KeyError(k)
            cur[k] = v
        if not cur:
            self.overrides.pop(designator, None)

    def clear_override(self, designator: str, fieldname: str | None = None) -> None:
        if fieldname is None:
            self.overrides.pop(designator, None)
        elif designator in self.overrides:
            self.overrides[designator].pop(fieldname, None)
            if not self.overrides[designator]:
                del self.overrides[designator]

    def is_overridden(self, designator: str, fieldname: str | None = None) -> bool:
        ov = self.overrides.get(designator, {})
        return bool(ov) if fieldname is None else fieldname in ov


@dataclass(frozen=True)
class SearchKey:
    kind: Kind
    value: float
    size: str
    spec: Spec


@dataclass
class Line:
    """A group of designators from one BOM row that share value, footprint and spec."""

    row: BomRow
    designators: list[str]
    kind: Kind
    value: float | None
    size: str | None
    spec: Spec
    boards: int

    @property
    def qty_per_board(self) -> int:
        # Respect the BOM's Quantity when the row is not split.
        if len(self.designators) == len(self.row.designators):
            return self.row.qty_per_board
        return len(self.designators)

    @property
    def needed(self) -> int:
        return self.qty_per_board * self.boards

    @property
    def key(self) -> SearchKey | None:
        if self.value is None or self.size is None:
            return None
        return SearchKey(self.kind, round(self.value, 15), self.size, self.spec)

    @property
    def problem(self) -> str | None:
        if self.value is None:
            return f"Could not parse value '{self.row.value_text}'"
        if self.size is None:
            return f"Could not determine chip size from footprint '{self.row.footprint}'"
        return None


def build_lines(bom: Bom, settings: Settings) -> list[Line]:
    lines: list[Line] = []
    for row in bom.pickable_rows:
        groups: dict[tuple, list[str]] = {}
        for d in row.designators:
            spec = settings.effective_spec(d, row.kind)
            value = settings.effective_value(d, row)
            groups.setdefault((spec, value), []).append(d)
        for (spec, value), designators in groups.items():
            lines.append(Line(row, designators, row.kind, value, row.size, spec, settings.boards))
    return lines


def parse_override(text: str) -> tuple[list[str], dict[str, object]]:
    """Parse 'C9,C10:dielectric=X5R,min_voltage=25' as used by headless --override."""
    if ":" not in text:
        raise ValueError(f"Override must look like DESIGNATORS:field=value — got {text!r}")
    left, right = text.split(":", 1)
    designators = [d.strip() for d in left.split(",") if d.strip()]
    fields: dict[str, object] = {}
    for part in right.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k = k.strip().lower().replace("-", "_")
        k = {"voltage": "min_voltage", "power": "min_power", "tol": "tolerance"}.get(k, k)
        if k not in SPEC_FIELDS and k != "value":
            raise ValueError(f"Unknown override field {k!r}")
        fields[k] = coerce_field(k, v.strip())
    return designators, fields


def coerce_field(name: str, raw: str) -> object:
    raw = raw.strip()
    if raw == "" or raw.lower() in ("any", "none"):
        return None
    if name == "dielectric":
        up = raw.upper().replace("NP0", "C0G").replace("COG", "C0G")
        return up
    if name == "min_power":
        return parse_power(raw)
    if name == "min_voltage":
        return float(raw.upper().rstrip("V"))
    if name == "tolerance":
        return float(raw.replace("±", "").rstrip("%"))
    if name == "value":
        return float(raw)
    raise KeyError(name)


def parse_power(raw: str) -> float:
    s = raw.strip().lower().replace(" ", "")
    if "/" in s:
        num, den = s.rstrip("w").split("/")
        return float(num) / float(den)
    if s.endswith("mw"):
        return float(s[:-2]) / 1000
    return float(s.rstrip("w"))


def field_text(name: str, value: object) -> str:
    """Display/edit form of a spec field value ('' means no requirement)."""
    if value is None:
        return ""
    if name == "dielectric":
        return str(value)
    return f"{float(value):g}"
