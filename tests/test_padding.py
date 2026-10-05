"""Basket padding: equal extra spend, price breaks, stock caps."""

import pytest

from cbapick4me.core.padding import ALREADY_MET, REACHED, UNREACHABLE, pad
from cbapick4me.core.pricing import Pick, line_cost, max_qty_within, unit_price
from cbapick4me.core.suppliers.base import Candidate


def _pick(breaks, qty, stock=100_000, mpn="X"):
    c = Candidate(mpn, "M", "S", f"{mpn}-ND", "", stock, breaks)
    return Pick(c, qty, qty, unit_price(breaks, qty), line_cost(breaks, qty))


def test_max_qty_within_uses_cheaper_break():
    breaks = [(1, 0.10), (100, 0.08)]
    # 9.00 buys 90 at 0.10, but 112 at 0.08
    assert max_qty_within(breaks, 9.0, at_least=1) == 112
    assert max_qty_within(breaks, 9.0, at_least=1, stock=95) == 90
    assert max_qty_within(breaks, 0.05, at_least=3) == 3


def test_equal_extra_spend_across_lines():
    picks = [_pick([(1, 0.10)], 10, mpn="A"), _pick([(1, 1.00)], 1, mpn="B"), _pick([(1, 0.01)], 100, mpn="C")]
    base = sum(p.line_total for p in picks)
    out = pad(picks, threshold=base + 30, current_total=base)
    assert out.status == REACHED
    assert out.total >= base + 30
    extras = [n.line_total - o.line_total for n, o in zip(out.picks, picks)]
    assert max(extras) - min(extras) <= 1.0  # within one unit of the dearest part
    assert all(9 <= e <= 11 for e in extras)
    assert [p.padded for p in out.picks] == [n.order_qty - o.order_qty for n, o in zip(out.picks, picks)]
    assert out.added == pytest.approx(out.total - base, abs=0.01)


def test_overshoot_is_small():
    picks = [_pick([(1, 2.50)], 1, mpn="A"), _pick([(1, 0.01)], 10, mpn="B")]
    base = sum(p.line_total for p in picks)
    out = pad(picks, threshold=base + 7, current_total=base)
    assert base + 7 <= out.total < base + 7 + 0.02  # finishes on the cheap line


def test_takes_the_better_break():
    picks = [_pick([(1, 0.10), (100, 0.05)], 50)]
    out = pad(picks, threshold=5.0, current_total=5.0 - 0.01)
    # 51 units would cost 5.10, but 100 at the next break costs 5.00 (more parts, less money)
    assert out.picks[0].order_qty >= 100


def test_stock_cap_shifts_spend_to_other_lines():
    capped = _pick([(1, 1.0)], 1, stock=3, mpn="A")
    free = _pick([(1, 1.0)], 1, mpn="B")
    out = pad([capped, free], threshold=12, current_total=2)
    assert out.status == REACHED
    assert out.picks[0].order_qty == 3
    assert out.picks[1].order_qty == 9


def test_unreachable_and_already_met():
    p = _pick([(1, 1.0)], 1, stock=4)
    out = pad([p], threshold=100, current_total=1)
    assert out.status == UNREACHABLE and out.picks[0].order_qty == 4 and out.total == 4
    out = pad([p], threshold=1, current_total=50)
    assert out.status == ALREADY_MET and out.picks[0] is p
