"""Parsing of component values, footprints and designators."""

from __future__ import annotations

import math
import re
from enum import Enum


class Kind(str, Enum):
    CAPACITOR = "capacitor"
    RESISTOR = "resistor"
    OTHER = "other"


_DESIGNATOR_KIND = [
    (re.compile(r"^C\d+$", re.I), Kind.CAPACITOR),
    (re.compile(r"^R\d+$", re.I), Kind.RESISTOR),
]


def classify(designator: str) -> Kind:
    d = designator.strip()
    for pattern, kind in _DESIGNATOR_KIND:
        if pattern.match(d):
            return kind
    return Kind.OTHER


def split_designators(field: str) -> list[str]:
    return [d.strip() for d in re.split(r"[,;\s]+", field) if d.strip()]


_SI = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "R": 1.0,
    "r": 1.0,
    "": 1.0,
}

# "4k7", "2R2", "1n5"
_INFIX = re.compile(r"^(\d+)([pnuµμmkKMGRr])(\d+)$")
# "4.7k", "100nF", "10", "0.1u", "10 kΩ"
_PLAIN = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)\s*([pnuµμmkKMGRr]?)$")


def parse_value(text: str, kind: Kind) -> float | None:
    """Parse a capacitor (farads) or resistor (ohms) value. Returns None if unparseable."""
    s = text.strip().replace(" ", "")
    if kind is Kind.CAPACITOR:
        s = re.sub(r"[fF]$", "", s)
    elif kind is Kind.RESISTOR:
        s = re.sub(r"(?i)(ohms?|Ω)$", "", s)
        if s in ("0", "0R", "0r"):
            return 0.0
    if not s:
        return None
    if m := _INFIX.match(s):
        whole, unit, frac = m.groups()
        return float(f"{whole}.{frac}") * _SI[unit]
    if m := _PLAIN.match(s):
        num, unit = m.groups()
        if kind is Kind.CAPACITOR and unit in ("R", "r"):
            return None
        return float(num) * _SI[unit]
    return None


# Imperial chip sizes and their metric equivalents.
METRIC_TO_IMPERIAL = {
    "0402": "01005",
    "0603": "0201",
    "1005": "0402",
    "1608": "0603",
    "2012": "0805",
    "3216": "1206",
    "3225": "1210",
    "4532": "1812",
    "5025": "2010",
    "6332": "2512",
}
IMPERIAL_SIZES = ["01005", "0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512"]
IMPERIAL_TO_METRIC = {v: k for k, v in METRIC_TO_IMPERIAL.items()}

_METRIC_TAGGED = re.compile(r"(\d{4})_?Metric", re.I)
_SIZE_TOKEN = re.compile(r"(?<!\d)(01005|\d{4})(?!\d)")


def parse_footprint(footprint: str) -> str | None:
    """Return the imperial chip size code (e.g. "0603") for a footprint name, or None."""
    fp = footprint.strip()
    # KiCad-style "R_0805_2012Metric": prefer the explicit imperial token.
    tagged = _METRIC_TAGGED.search(fp)
    tokens = _SIZE_TOKEN.findall(_METRIC_TAGGED.sub("", fp))
    for tok in tokens:
        if tok in IMPERIAL_SIZES:
            return tok
    if tagged and tagged.group(1) in METRIC_TO_IMPERIAL:
        return METRIC_TO_IMPERIAL[tagged.group(1)]
    for tok in tokens:
        if tok in METRIC_TO_IMPERIAL:
            return METRIC_TO_IMPERIAL[tok]
    return None


def format_value(value: float, kind: Kind) -> str:
    """Human/keyword-friendly value string: 100nF, 4.7µF, 10kΩ, 49.9Ω."""
    if kind is Kind.CAPACITOR:
        for unit, mult in (("µF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
            if value >= mult * 0.9999:
                return f"{_trim(value / mult)}{unit}"
        return f"{_trim(value / 1e-12)}pF"
    for unit, mult in (("MΩ", 1e6), ("kΩ", 1e3)):
        if value >= mult:
            return f"{_trim(value / mult)}{unit}"
    return f"{_trim(value)}Ω"


def keyword_value(value: float, kind: Kind) -> str:
    """ASCII form for supplier keyword searches: 100nF, 4.7uF, 10k, 49.9 Ohms."""
    s = format_value(value, kind)
    if kind is Kind.CAPACITOR:
        return s.replace("µ", "u")
    if s.endswith("MΩ"):
        return s[:-1]
    if s.endswith("kΩ"):
        return s[:-1]
    return s[:-1] + " Ohms"


def keyword_values(value: float, kind: Kind) -> list[str]:
    """Every spelling a supplier might use for this value, best first.

    Distributors only match the spelling in their own descriptions — DigiKey
    lists 100nF as "0.1UF" and 10nF as "10000PF" — so capacitors are searched
    as µF, then pF, then nF.
    """
    if kind is not Kind.CAPACITOR:
        return [keyword_value(value, kind)]
    forms = []
    if value >= 1e-9:
        forms.append(f"{value / 1e-6:.6g}uF")
    if value < 1e-6:
        forms.append(f"{value / 1e-12:.6g}pF")
    if 1e-9 <= value < 1e-6:
        forms.append(f"{value / 1e-9:.6g}nF")
    return list(dict.fromkeys(forms))


def _trim(x: float) -> str:
    return f"{x:.4g}"


def values_equal(a: float, b: float) -> bool:
    if a == 0 or b == 0:
        return a == b
    return math.isclose(a, b, rel_tol=1e-3)


# --- E-series -------------------------------------------------------------

E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
       1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
       1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
       3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
       4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
       7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76]


def nearest_standard(value: float) -> list[float]:
    """Nearest E96 and E24 values (distinct, closest first) for a non-standard resistance."""
    if value <= 0:
        return []
    decade = 10 ** math.floor(math.log10(value))
    out: set[float] = set()
    for series in (E96, E24):
        cands = [m * d for m in series for d in (decade / 10, decade, decade * 10)]
        best = min(cands, key=lambda c: abs(math.log(c / value)))
        out.add(round(best, 6))
    out.discard(round(value, 6))
    return sorted(out, key=lambda c: abs(c - value))


def is_standard_resistance(value: float) -> bool:
    if value <= 0:
        return True
    mant = value / 10 ** math.floor(math.log10(value))
    return any(math.isclose(mant, m, rel_tol=1e-3) for m in E96 + E24) or math.isclose(mant, 10, rel_tol=1e-3)
