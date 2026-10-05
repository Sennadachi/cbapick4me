import csv
import io
import threading
import time

import pytest

from cbapick4me.core.bom import parse_bom
from cbapick4me.core.cache import SearchCache
from cbapick4me.core.parse import Kind
from cbapick4me.core.picker import pick, render_output, total_cost
from cbapick4me.core.session import Session
from cbapick4me.core.specs import Settings, build_lines, parse_override
from .fake_supplier import FakeSupplier


def _read_output(data: bytes) -> list[dict]:
    assert data.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(data[3:].decode("utf-8"))))


def test_wayfinder_is_parsed(wayfinder_bytes):
    bom = parse_bom(wayfinder_bytes, "BOM_Wayfinder.csv")
    assert bom.columns["value"] == "Name"
    assert len(bom.pickable_rows) == 14
    assert len(bom.rows) - len(bom.pickable_rows) == 22
    caps = [r for r in bom.pickable_rows if r.kind is Kind.CAPACITOR]
    assert sum(len(r.designators) for r in caps) == 22
    res = [r for r in bom.pickable_rows if r.kind is Kind.RESISTOR]
    assert sum(len(r.designators) for r in res) == 25
    assert all(r.value is not None and r.size for r in bom.pickable_rows)
    assert bom.output_name() == "BOM_Wayfinder_picked4u.csv"


def test_utf8_comma_bom():
    text = (
        "Comment,Designator,Footprint,Quantity,Manufacturer Part\r\n"
        "100nF,\"C1,C2\",C_0603_1608Metric,2,\r\n"
        "10k,R1,R_0402_1005Metric,1,\r\n"
        "ESP32,U1,QFN,1,ESP32-C3\r\n"
    )
    bom = parse_bom(b"\xef\xbb\xbf" + text.encode(), "pro.csv")
    assert [r.size for r in bom.pickable_rows] == ["0603", "0402"]
    assert bom.rows[2].kind is Kind.OTHER


def test_override_splits_grouped_row(wayfinder_bytes):
    bom = parse_bom(wayfinder_bytes, "w.csv")
    s = Settings(boards=3)
    s.set_override("C9", dielectric="X5R")
    lines = [ln for ln in build_lines(bom, s) if "C9" in ln.designators or "C10" in ln.designators]
    assert [(ln.designators, ln.spec.dielectric, ln.needed) for ln in lines] == [
        (["C9"], "X5R", 3),
        (["C10"], "X7R", 3),
    ]


def test_parse_override():
    des, fields = parse_override("C9,C10:dielectric=np0,voltage=25V")
    assert des == ["C9", "C10"] and fields == {"dielectric": "C0G", "min_voltage": 25.0}
    _, fields = parse_override("R4:power=1/8W,tol=1%")
    assert fields == {"min_power": 0.125, "tolerance": 1.0}


def test_full_pick_and_output_round_trip(wayfinder_bytes):
    bom = parse_bom(wayfinder_bytes, "w.csv")
    s = Settings(boards=5)
    s.set_override("C9", dielectric="X5R")
    results = pick(bom, s, FakeSupplier())
    rows = _read_output(render_output(bom, results, s))

    # pass-through rows survive, including CJK manufacturer names
    d3 = next(r for r in rows if r["Designator"] == "D3")
    assert d3["Manufacturer"] == "DIODES(美台)" and d3["Supplier Part"] == "C109094"
    assert d3["Quantity"] == "5"
    assert next(r for r in rows if r["Designator"] == "D1")["Manufacturer"] == "null"

    # C9/C10 split into two rows with different specs
    assert any(r["Designator"] == "C9" and "X5R" in r["Pick Notes"] for r in rows)
    assert any(r["Designator"] == "C10" and "X7R" in r["Pick Notes"] for r in rows)

    # picked rows are filled in; order qty ≥ needed
    c1 = next(r for r in rows if r["Designator"].startswith("C1,"))
    assert c1["Manufacturer Part"].startswith("FAKE-C1206")
    assert c1["Supplier"] == "Fake"
    assert int(c1["Order Qty"]) >= int(c1["Qty Needed"]) == 40
    assert c1["Quantity"] == c1["Order Qty"]

    # 50 Ω has no exact match and suggests 49.9 Ω
    r4 = next(r for r in rows if r["Designator"].startswith("R4"))
    assert r4["Pick Notes"].startswith("NOT PICKED") and "49.9Ω" in r4["Pick Notes"]
    assert len(rows) == 37  # 36 original + 1 split
    # IDs stay unique and sequential after the split
    assert [r["ID"] for r in rows] == [str(n) for n in range(1, 38)]
    assert [r["ID"] for r in rows if r["Designator"] in ("C9", "C10")] == ["3", "4"]


def test_session_accept_nearest(wayfinder_bytes, fake_supplier):
    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    sess.run()
    assert sum(not r.ok for r in sess.results) == 1
    assert sess.accept_all_nearest()
    sess.run()
    assert all(r.ok for r in sess.results)
    r4 = next(r for r in sess.results if "R4" in r.line.designators)
    assert r4.line.value == 49.9
    out = _read_output(sess.output_bytes())
    row = next(r for r in out if r["Designator"].startswith("R4"))
    assert row["Name"] == "49.9Ω" and "Value changed from 50" in row["Pick Notes"]


def test_session_requires_keys(wayfinder_bytes):
    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    assert "credentials" in sess.check_ready()


def test_cache_dedupes_concurrent_fetches(tmp_path):
    cache = SearchCache(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        time.sleep(0.1)
        return {"x": 1}

    threads = [threading.Thread(target=cache.get_or_fetch, args=("k", fetch)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    # persisted to disk for a fresh instance
    assert SearchCache(tmp_path).get("k") == {"x": 1}
    assert SearchCache(tmp_path, ttl=0).get("k") is None


def test_apply_exception_to_several_of_one_type(wayfinder_bytes):
    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    sess.apply_exception(["C1", "C5", "C9"], {"dielectric": "X5R", "min_voltage": 25.0})
    lines = {tuple(ln.designators): ln.spec for ln in sess.lines()}
    assert lines[("C1",)].dielectric == "X5R" and lines[("C1",)].min_voltage == 25
    assert lines[("C2", "C3", "C4", "C6", "C13", "C14", "C18")].dielectric == "X7R"


def test_apply_exception_rejects_mixed_types(wayfinder_bytes):
    import pytest

    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    with pytest.raises(ValueError, match="mixes capacitors and resistors"):
        sess.apply_exception(["C1", "R1"], {"tolerance": 5.0})
    with pytest.raises(ValueError, match="U1"):
        sess.apply_exception(["U1"], {"tolerance": 5.0})
    assert sess.settings.overrides == {}


def test_currency_follows_site_unless_set():
    from cbapick4me.core.config import Config

    assert Config(digikey_site="UK").effective_currency == "GBP"
    assert Config(digikey_site="DE").effective_currency == "EUR"
    assert Config(digikey_site="UK", currency="usd").effective_currency == "USD"
    assert Config().effective_currency == "USD"


def test_bad_keys_stop_the_run_with_one_clear_error(wayfinder_bytes, fake_supplier):
    import pytest

    from cbapick4me.core.suppliers.base import SupplierAuthError

    class RejectingSupplier(FakeSupplier):
        def search(self, key):
            super().search(key)
            raise SupplierAuthError("API keys rejected (401): Invalid clientId")

    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    sess._supplier, sess._supplier_sig = RejectingSupplier(), None
    sess.supplier = lambda: sess._supplier
    with pytest.raises(SupplierAuthError, match="Invalid clientId"):
        sess.run()
    assert sess.results == []


def _padding_session(wayfinder_bytes, threshold=100.0):
    sess = Session()
    sess.settings.pad_enabled = True
    sess.settings.pad_threshold = threshold
    sess.load_bytes(wayfinder_bytes, "w.csv")
    sess.run()
    return sess


def test_padding_prices_other_lines_by_mpn(wayfinder_bytes, fake_supplier):
    sess = _padding_session(wayfinder_bytes)
    assert len(sess.extras) == 22 and all(x.ok for x in sess.extras)
    u1 = next(x for x in sess.extras if x.designators == ["U1"])
    assert u1.query == "ESP32-C6-MINI-1-N4" and u1.pick.candidate.mpn == "FAKE-ESP32-C6-MINI-1-N4"
    # rows with no MPN fall back to their value text
    assert next(x for x in sess.extras if x.designators == ["U3"]).query == "TC2030-IDC-NL"
    basket = sess.basket()
    assert len(basket) == len(sess.results) + 22
    assert [x.row.index for x in basket] == sorted(x.row.index for x in basket)
    assert sess.total > total_cost(sess.results)


def test_padding_reaches_threshold_and_is_written(wayfinder_bytes, fake_supplier):
    sess = _padding_session(wayfinder_bytes)
    basket = sess.basket()
    chosen = [i for i, x in enumerate(basket) if {"R18", "C5", "U6"} & set(x.designators)]
    start = sess.total
    outcome = sess.pad(chosen)
    assert outcome.status == "reached"
    assert 100 <= sess.total < 101
    assert outcome.added == pytest.approx(sess.total - start, abs=0.01)
    extras = [basket[i].pick.line_total - basket[i].base_pick.line_total for i in chosen]
    assert max(extras) - min(extras) < 5  # U6 costs ~£1 each; spends stay close

    rows = _read_output(sess.output_bytes())
    u6 = next(r for r in rows if r["Designator"] == "U6")
    assert u6["Manufacturer Part"] == "FAKE-AP2112K-3.3TRG1" and u6["Supplier"] == "Fake"
    assert "Priced by MPN" in u6["Pick Notes"] and "padding" in u6["Pick Notes"]
    assert int(u6["Order Qty"]) > 1 and u6["Quantity"] == u6["Order Qty"]
    r18_line = next(basket[i] for i in chosen if "R18" in basket[i].designators)
    r18 = next(r for r in rows if r["Designator"] == "R18,R19")
    assert f"+{r18_line.pick.padded} padding" in r18["Pick Notes"]

    # choosing another part for a line drops its padding; clearing drops the rest
    ri = next(i for i, r in enumerate(sess.results) if "R18" in r.designators)
    sess.select(ri, 1)
    assert sess.results[ri].pad is None
    sess.clear_padding()
    assert all(x.pad is None for x in sess.basket())


def test_no_lookups_without_padding(wayfinder_bytes, fake_supplier):
    sess = Session()
    sess.load_bytes(wayfinder_bytes, "w.csv")
    sess.run()
    assert sess.extras == [] and sess.supplier().lookups == []
    u6 = next(r for r in _read_output(sess.output_bytes()) if r["Designator"] == "U6")
    assert u6["Supplier"] == "LCSC" and u6["Pick Notes"] == ""
