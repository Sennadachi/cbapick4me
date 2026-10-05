"""Supplier attribute parsing and spec matching shared by all suppliers."""

from __future__ import annotations

import re

from .parse import IMPERIAL_SIZES, METRIC_TO_IMPERIAL, Kind, parse_value, values_equal
from .specs import DIELECTRIC_ALIASES, SearchKey, parse_power
from .suppliers.base import Candidate


def attr_voltage(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k?)V", text)
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2) else 1)


def attr_power(text: str) -> float | None:
    # DigiKey: "0.1W, 1/10W"; descriptions: "1/10W" or "100mW".
    m = re.search(r"(\d+(?:\.\d+)?)\s*(m?)W", text, re.I)
    frac = re.search(r"(\d+)\s*/\s*(\d+)\s*W", text, re.I)
    if m and not (frac and frac.start() < m.start()):
        return float(m.group(1)) / (1000 if m.group(2).lower() == "m" else 1)
    if frac:
        return parse_power(f"{frac.group(1)}/{frac.group(2)}W")
    return None


def attr_tolerance(text: str) -> float | None:
    m = re.search(r"±?\s*(\d+(?:\.\d+)?)\s*%", text)
    return float(m.group(1)) if m else None


def attr_dielectric(text: str) -> str | None:
    up = text.upper().replace("COG", "C0G").replace("NPO", "NP0")
    for canonical, aliases in DIELECTRIC_ALIASES.items():
        if any(a in up for a in aliases):
            return canonical
    m = re.search(r"\b([XYZ]\d[A-Z])\b", up)
    return m.group(1) if m else None


def attr_size(text: str) -> str | None:
    # DigiKey: "0603 (1608 Metric)"; a description may contain "0603" somewhere.
    m = re.search(r"\b(\d{4,5})\s*\((\d{4})\s*Metric\)", text, re.I)
    if m and m.group(1) in IMPERIAL_SIZES:
        return m.group(1)
    for tok in re.findall(r"(?<![\d.])(01005|\d{4})(?![\d.])", text):
        if tok in IMPERIAL_SIZES:
            return tok
    m = re.search(r"(\d{4})\s*Metric", text, re.I)
    if m:
        return METRIC_TO_IMPERIAL.get(m.group(1))
    return None


def attr_value(text: str, kind: Kind) -> float | None:
    """Parse '0.1 µF', '100nF', '10 kOhms', '49.9 Ohms' style values."""
    s = text.strip()
    if kind is Kind.RESISTOR:
        s = re.sub(r"\s*(?:k|K)\s*Ohms?$", "k", s)
        s = re.sub(r"\s*M\s*Ohms?$", "M", s)
        s = re.sub(r"\s*(?:Ohms?|Ω)$", "", s, flags=re.I)
        s = s.replace("kΩ", "k").replace("MΩ", "M")
    return parse_value(s, kind)


def mismatch(c: Candidate, key: SearchKey) -> str | None:
    """Return why a candidate does not satisfy the key, or None if it matches."""
    a = c.attrs
    if not a.get("family_ok", True):
        return "family"
    value = a.get("value")
    if value is None or not values_equal(float(value), key.value):
        return "value"
    if a.get("size") != key.size:
        return "size"
    spec = key.spec
    if key.kind is Kind.CAPACITOR:
        if spec.dielectric and a.get("dielectric") != spec.dielectric:
            return "dielectric"
        if spec.min_voltage and (a.get("voltage") is None or a["voltage"] < spec.min_voltage):
            return "voltage"
    if key.kind is Kind.RESISTOR:
        if spec.min_power and (a.get("power") is None or a["power"] < spec.min_power - 1e-9):
            return "power"
    if spec.tolerance and (a.get("tolerance") is None or a["tolerance"] > spec.tolerance + 1e-9):
        return "tolerance"
    return None


def filter_candidates(cands: list[Candidate], key: SearchKey) -> list[Candidate]:
    return [c for c in cands if mismatch(c, key) is None]
