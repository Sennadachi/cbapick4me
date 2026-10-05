"""Offline fake supplier with synthetic parts, standing in for DigiKey in tests.

Every part number starts with "FAKE-" so its output can never be mistaken for real data.
"""

from __future__ import annotations

import hashlib

from cbapick4me.core.parse import Kind, format_value, is_standard_resistance
from cbapick4me.core.specs import SearchKey
from cbapick4me.core.suppliers.base import Candidate

VOLTAGES = [6.3, 10, 16, 25, 50, 100]
POWERS = {"0201": 0.05, "0402": 0.0625, "0603": 0.1, "0805": 0.125, "1206": 0.25, "1210": 0.5, "2512": 1.0}


def _rand(*parts: object) -> float:
    h = hashlib.sha256(repr(parts).encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


class FakeSupplier:
    name = "Demo"

    def __init__(self) -> None:
        self.calls: list[SearchKey] = []
        self.lookups: list[str] = []

    def check(self) -> str:
        return "Fake supplier needs no keys"

    def search(self, key: SearchKey) -> list[Candidate]:
        self.calls.append(key)
        if key.kind is Kind.RESISTOR and key.value and not is_standard_resistance(key.value):
            return []
        out = []
        for i in range(6):
            r = _rand(key.kind, key.value, key.size, i)
            base = (0.08 if key.kind is Kind.CAPACITOR else 0.05) * (0.6 + r)
            breaks = [(1, round(base, 4)), (10, round(base * 0.55, 4)), (100, round(base * 0.18, 4)), (1000, round(base * 0.07, 4))]
            attrs: dict[str, object] = {"value": key.value, "size": key.size, "family_ok": True}
            if key.kind is Kind.CAPACITOR:
                attrs["dielectric"] = key.spec.dielectric or "X7R"
                attrs["voltage"] = VOLTAGES[int(r * len(VOLTAGES))]
                attrs["tolerance"] = 10.0
                mpn = f"FAKE-C{key.size}-{format_value(key.value, key.kind)}-{i}"
            else:
                attrs["power"] = POWERS.get(key.size, 0.1)
                attrs["tolerance"] = 1.0 if i % 2 == 0 else 5.0
                mpn = f"FAKE-R{key.size}-{format_value(key.value, key.kind)}-{i}"
            out.append(
                Candidate(
                    mpn=mpn,
                    manufacturer="Fake Corp",
                    supplier="Fake",
                    supplier_part=f"{mpn}-CT",
                    description=f"Fake part {i}",
                    stock=int(500 + r * 200_000),
                    price_breaks=breaks,
                    packaging="Cut Tape",
                    attrs=attrs,
                )
            )
        return out

    def lookup(self, mpn: str, supplier_part: str = "") -> list[Candidate]:
        self.lookups.append(mpn)
        r = _rand("lookup", mpn)
        base = 0.3 + r * 4.7
        breaks = [(1, round(base, 3)), (10, round(base * 0.9, 3)), (100, round(base * 0.75, 3))]
        return [
            Candidate(
                mpn=f"FAKE-{mpn}",
                manufacturer="Fake Corp",
                supplier="Fake",
                supplier_part=f"FAKE-{mpn}-ND",
                description="Fake part looked up by MPN",
                stock=int(200 + r * 5000),
                price_breaks=breaks,
                packaging="Cut Tape",
            )
        ]
