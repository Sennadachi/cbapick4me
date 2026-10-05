"""Parsing of DigiKey API responses (recorded-shape samples, no network)."""

from cbapick4me.core.match import attr_power, attr_size, mismatch
from cbapick4me.core.parse import Kind
from cbapick4me.core.specs import SearchKey, Spec
from cbapick4me.core.suppliers.digikey import build_queries as dk_queries
from cbapick4me.core.suppliers.digikey import parse_product

DK_CAP = {
    "Description": {"ProductDescription": "CAP CER 0.1UF 50V X7R 0603"},
    "Manufacturer": {"Name": "Samsung Electro-Mechanics"},
    "ManufacturerProductNumber": "CL10B104KB8NNNC",
    "ProductUrl": "https://www.digikey.com/en/products/detail/x",
    "ProductStatus": {"Status": "Active"},
    "Parameters": [
        {"ParameterText": "Capacitance", "ValueText": "0.1 µF"},
        {"ParameterText": "Tolerance", "ValueText": "±10%"},
        {"ParameterText": "Voltage - Rated", "ValueText": "50V"},
        {"ParameterText": "Temperature Coefficient", "ValueText": "X7R"},
        {"ParameterText": "Package / Case", "ValueText": "0603 (1608 Metric)"},
    ],
    "ProductVariations": [
        {
            "DigiKeyProductNumber": "1276-1000-1-ND",
            "PackageType": {"Name": "Cut Tape (CT)"},
            "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1}, {"BreakQuantity": 100, "UnitPrice": 0.0122}],
            "QuantityAvailableforPackageType": 1234567,
            "MinimumOrderQuantity": 1,
        },
        {
            "DigiKeyProductNumber": "1276-1000-2-ND",
            "PackageType": {"Name": "Tape & Reel (TR)"},
            "StandardPricing": [{"BreakQuantity": 4000, "UnitPrice": 0.0035}],
            "QuantityAvailableforPackageType": 800000,
            "MinimumOrderQuantity": 4000,
        },
        {
            "DigiKeyProductNumber": "1276-1000-6-ND",
            "PackageType": {"Name": "Digi-Reel®"},
            "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1}],
            "QuantityAvailableforPackageType": 1234567,
            "MinimumOrderQuantity": 1,
        },
    ],
}

DK_RES = {
    "Description": {"ProductDescription": "RES 10K OHM 1% 1/10W 0603"},
    "Manufacturer": {"Name": "YAGEO"},
    "ManufacturerProductNumber": "RC0603FR-0710KL",
    "ProductStatus": {"Status": "Active"},
    "Parameters": [
        {"ParameterText": "Resistance", "ValueText": "10 kOhms"},
        {"ParameterText": "Tolerance", "ValueText": "±1%"},
        {"ParameterText": "Power (Watts)", "ValueText": "0.1W, 1/10W"},
        {"ParameterText": "Package / Case", "ValueText": "0603 (1608 Metric)"},
    ],
    "ProductVariations": [
        {
            "DigiKeyProductNumber": "311-10.0KHRCT-ND",
            "PackageType": {"Name": "Cut Tape (CT)"},
            "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1}],
            "QuantityAvailableforPackageType": 999999,
            "MinimumOrderQuantity": 1,
        }
    ],
}

CAP_KEY = SearchKey(Kind.CAPACITOR, 1e-7, "0603", Spec(dielectric="X7R", min_voltage=25))
RES_KEY = SearchKey(Kind.RESISTOR, 10_000.0, "0603", Spec(min_power=0.1, tolerance=1.0))


def test_digikey_capacitor_variations():
    cands = parse_product(DK_CAP, Kind.CAPACITOR)
    assert [c.supplier_part for c in cands] == ["1276-1000-1-ND", "1276-1000-2-ND"]  # no Digi-Reel
    c = cands[0]
    assert c.attrs["value"] == 1e-7 and c.attrs["voltage"] == 50 and c.attrs["dielectric"] == "X7R"
    assert c.attrs["size"] == "0603" and c.attrs["tolerance"] == 10
    assert mismatch(c, CAP_KEY) is None
    assert mismatch(c, SearchKey(Kind.CAPACITOR, 1e-7, "0603", Spec(dielectric="C0G"))) == "dielectric"
    assert mismatch(c, SearchKey(Kind.CAPACITOR, 1e-7, "0603", Spec(min_voltage=100))) == "voltage"
    assert mismatch(c, SearchKey(Kind.CAPACITOR, 1e-7, "0805", Spec())) == "size"


def test_digikey_resistor():
    (c,) = parse_product(DK_RES, Kind.RESISTOR)
    assert c.attrs["value"] == 10_000 and c.attrs["power"] == 0.1 and c.attrs["tolerance"] == 1
    assert mismatch(c, RES_KEY) is None
    assert mismatch(c, SearchKey(Kind.RESISTOR, 10_000.0, "0603", Spec(min_power=0.125))) == "power"


def test_digikey_skips_obsolete():
    assert parse_product({**DK_RES, "ProductStatus": {"Status": "Obsolete"}}, Kind.RESISTOR) == []


def test_helpers():
    assert attr_power("1/8W") == 0.125 and attr_power("100mW") == 0.1
    assert attr_size("2012 Metric") == "0805"


def test_queries_try_every_capacitor_spelling():
    # DigiKey finds nothing for "100nF" — its descriptions say "0.1UF".
    assert dk_queries(CAP_KEY) == ["CAP CER 0.1uF 0603 X7R", "CAP CER 100000pF 0603 X7R", "CAP CER 100nF 0603 X7R"]
    assert dk_queries(RES_KEY) == ["RES 10k 0603 1%"]


def test_search_falls_back_to_next_spelling_and_merges():
    from cbapick4me.core.suppliers.base import search_queries

    good = parse_product(DK_CAP, Kind.CAPACITOR)
    pages = {"0.1uF": [], "100000pF": good, "100nF": good}
    asked = []

    def fetch(query, page):
        asked.append(query)
        cands = next(v for k, v in pages.items() if k in query)
        return cands, len(cands), len(cands)

    found = search_queries(dk_queries(CAP_KEY), fetch, CAP_KEY, page_size=50, max_pages=3)
    assert asked == ["CAP CER 0.1uF 0603 X7R", "CAP CER 100000pF 0603 X7R", "CAP CER 100nF 0603 X7R"]
    assert sorted(c.supplier_part for c in found) == ["1276-1000-1-ND", "1276-1000-2-ND"]  # deduped


def test_auth_error_carries_supplier_message():
    import pytest
    import requests

    from cbapick4me.core.suppliers.base import SupplierAuthError
    from cbapick4me.core.suppliers.http import request_json

    class FakeSession(requests.Session):
        def request(self, method, url, **kw):
            r = requests.Response()
            r.status_code = 401
            r._content = b'{"StatusCode":401,"ErrorMessage":"Invalid clientId","ErrorDetails":"client_id form parameter has incorrect value"}'
            return r

    with pytest.raises(SupplierAuthError, match="Invalid clientId — client_id form parameter"):
        request_json(FakeSession(), "POST", "https://api.digikey.com/v1/oauth2/token")


def test_digikey_currency():
    assert parse_product(DK_RES, Kind.RESISTOR, "GBP")[0].currency == "GBP"


def test_other_parts_parse_without_spec_attributes():
    ic = {**DK_RES, "Description": {"ProductDescription": "IC REG LINEAR 3.3V 600MA SOT25"}, "Parameters": []}
    cands = parse_product(ic, Kind.OTHER, "GBP")
    assert [c.supplier_part for c in cands] == ["311-10.0KHRCT-ND"]
    assert cands[0].attrs == {} and cands[0].currency == "GBP"


def test_exact_mpn_match():
    from cbapick4me.core.suppliers.base import Candidate, exact_mpn

    def cand(mpn, spn):
        return Candidate(mpn, "M", "DigiKey", spn, "", 10, [(1, 1.0)])

    cands = [cand("AP2112K-3.3TRG1", "A-1"), cand("ap2112k-3.3 trg1", "A-2"), cand("AP2112K-3.3TRG1DI", "B")]
    assert [c.supplier_part for c in exact_mpn(cands, "AP2112K-3.3TRG1")] == ["A-1", "A-2"]
    # A matching supplier part number brings in its MPN even when the BOM's MPN is spelled differently.
    assert [c.supplier_part for c in exact_mpn(cands, "AP2112K", "B")] == ["B"]


def test_digikey_lookup_tries_supplier_part_then_mpn(monkeypatch):
    from cbapick4me.core.suppliers.digikey import DigiKey

    dk = DigiKey("id", "secret")
    seen = []

    def fake_page(keywords, offset):
        seen.append(keywords)
        return {"Products": [DK_RES] if keywords == "RC0603FR-0710KL" else []}

    monkeypatch.setattr(dk, "_fetch_page", fake_page)
    hits = dk.lookup("RC0603FR-0710KL", "311-OLD-ND")
    assert seen == ["311-OLD-ND", "RC0603FR-0710KL"]
    assert [c.supplier_part for c in hits] == ["311-10.0KHRCT-ND"]
    assert dk.lookup("NOPE-123") == []
