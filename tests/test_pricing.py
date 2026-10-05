import pytest

from cbapick4me.core.pricing import choose_order_qty, line_cost, rank, unit_price
from cbapick4me.core.suppliers.base import Candidate


def test_unit_price_and_line_cost():
    breaks = [(1, 0.10), (10, 0.05), (100, 0.018)]
    assert unit_price(breaks, 1) == 0.10
    assert unit_price(breaks, 9) == 0.10
    assert unit_price(breaks, 10) == 0.05
    assert unit_price(breaks, 250) == 0.018
    assert unit_price([(10, 0.1)], 5) is None
    assert line_cost(breaks, 100) == pytest.approx(1.8)


def test_order_100_instead_of_7():
    # 7 × $0.10 = $0.70; 100 × $0.018 = $1.80 → +$1.10, within the $2 spare budget.
    qty, up, total = choose_order_qty([(1, 0.10), (100, 0.018)], 7, cheap_threshold=0.2, spare_budget=2.0)
    assert (qty, up, total) == (100, 0.018, pytest.approx(1.8))


def test_free_spares_on_tie_even_when_not_cheap():
    # 9 × $1 = $9 = 10 × $0.90; take 10.
    qty, _, total = choose_order_qty([(1, 1.0), (10, 0.9)], 9, cheap_threshold=0.1, spare_budget=0)
    assert qty == 10 and total == pytest.approx(9.0)


def test_no_spares_for_expensive_parts():
    qty, _, _ = choose_order_qty([(1, 0.5), (100, 0.2)], 7, cheap_threshold=0.1, spare_budget=50)
    assert qty == 7


def test_spare_budget_limits_cumulative_extra():
    breaks = [(1, 0.05), (100, 0.01), (1000, 0.004), (10000, 0.002)]
    qty, _, total = choose_order_qty(breaks, 20, cheap_threshold=0.1, spare_budget=2.0)
    # 20→$1.00, 100→$1.00 (tie, free), 1000→$4.00 (+$3 > $2) so stop at 100.
    assert qty == 100 and total == pytest.approx(1.0)


def test_higher_break_cheaper_than_needed():
    # Buying 10 at the 10-break is cheaper than 8 at the 1-break.
    qty, _, total = choose_order_qty([(1, 0.5), (10, 0.3)], 8, cheap_threshold=0, spare_budget=0)
    assert qty == 10 and total == pytest.approx(3.0)


def test_moq_and_stock_respected():
    assert choose_order_qty([(1, 0.1)], 5, moq=50)[0] == 50
    assert choose_order_qty([(1, 0.01), (1000, 0.001)], 5, stock=500, cheap_threshold=1, spare_budget=10)[0] == 5
    assert choose_order_qty([(1, 0.1)], 600, stock=500) is None


def _cand(mpn, stock, breaks):
    return Candidate(mpn, "M", "S", mpn + "-ND", "", stock, breaks)


def test_rank_prefers_cheapest_then_stock_and_applies_stock_floor():
    a = _cand("A", 1_000_000, [(1, 0.10), (100, 0.02)])
    b = _cand("B", 50, [(1, 0.01)])  # cheapest but below 10× floor
    c = _cand("C", 5_000, [(1, 0.10), (100, 0.02)])
    picks, note = rank([a, b, c], 10, stock_factor=10, spare_budget=2.0)
    assert [p.candidate.mpn for p in picks] == ["A", "C"]
    assert note is None


def test_rank_relaxes_floor_when_needed():
    b = _cand("B", 50, [(1, 0.01)])
    picks, note = rank([b], 10, stock_factor=10)
    assert [p.candidate.mpn for p in picks] == ["B"]
    assert "relaxed" in note
