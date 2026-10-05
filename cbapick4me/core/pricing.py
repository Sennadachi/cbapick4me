"""Price-break maths, order-quantity selection and candidate ranking."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .suppliers.base import Candidate


@dataclass
class Pick:
    candidate: Candidate
    needed: int
    order_qty: int
    unit_price: float
    line_total: float
    # Extra units added by basket padding (included in order_qty).
    padded: int = 0

    @property
    def spares(self) -> int:
        return self.order_qty - self.needed - self.padded


def unit_price(breaks: list[tuple[int, float]], qty: int) -> float | None:
    """Unit price at the highest break <= qty; None if qty is below the first break."""
    price = None
    for brk, p in sorted(breaks):
        if brk <= qty:
            price = p
        else:
            break
    return price


def line_cost(breaks: list[tuple[int, float]], qty: int) -> float | None:
    p = unit_price(breaks, qty)
    return None if p is None else round(p * qty, 6)


def max_qty_within(breaks: list[tuple[int, float]], limit: float, *, at_least: int, stock: int | None = None) -> int:
    """Largest quantity ≥ at_least whose line cost is ≤ limit (at_least if none is).

    Worked out per price tier, so a higher break that buys more for the same money wins.
    """
    breaks = sorted(breaks)
    best = at_least
    for i, (brk, price) in enumerate(breaks):
        lo = max(brk, at_least)
        hi = breaks[i + 1][0] - 1 if i + 1 < len(breaks) else math.inf
        if stock is not None:
            hi = min(hi, stock)
        if price <= 0:
            q = hi
        else:
            q = min(hi, math.floor(limit / price + 1e-9))
        if q != math.inf and q >= lo:
            best = max(best, int(q))
    return best


def choose_order_qty(
    breaks: list[tuple[int, float]],
    needed: int,
    *,
    moq: int = 1,
    stock: int | None = None,
    cheap_threshold: float = 0.10,
    spare_budget: float = 2.00,
) -> tuple[int, float, float] | None:
    """Pick the order quantity: cheapest total, then add spares if the part is cheap.

    Returns (order_qty, unit_price, line_total) or None if it can't be bought.
    """
    if not breaks:
        return None
    breaks = sorted(breaks)
    minimum = max(needed, moq, breaks[0][0])
    options = sorted({minimum} | {b for b, _ in breaks if b >= minimum})
    if stock is not None:
        options = [q for q in options if q <= stock]
    if not options:
        return None

    costs = {q: line_cost(breaks, q) for q in options}
    best_cost = min(costs.values())
    # Cheapest total; on a tie take the larger quantity (free spares).
    best = max(q for q, c in costs.items() if c == best_cost)

    # Spares rule: for cheap parts, step up to higher breaks while the extra
    # spend over the cheapest option stays within the spare budget.
    if unit_price(breaks, best) < cheap_threshold:
        for q in options:
            if q > best and costs[q] - best_cost <= spare_budget + 1e-9:
                best = q

    return best, unit_price(breaks, best), costs[best]


def rank(
    candidates: list[Candidate],
    needed: int,
    *,
    stock_factor: float = 10.0,
    cheap_threshold: float = 0.10,
    spare_budget: float = 2.00,
) -> tuple[list[Pick], str | None]:
    """Rank candidates by line cost then stock. Returns (picks, note)."""
    note = None
    floor = max(needed, int(needed * stock_factor))
    pool = [c for c in candidates if c.stock >= floor]
    if not pool:
        pool = [c for c in candidates if c.stock >= needed]
        if pool:
            note = f"No part with ≥{floor} in stock; relaxed to ≥{needed}"

    picks: list[Pick] = []
    for c in pool:
        chosen = choose_order_qty(
            c.price_breaks,
            needed,
            moq=c.moq,
            stock=c.stock,
            cheap_threshold=cheap_threshold,
            spare_budget=spare_budget,
        )
        if chosen:
            qty, up, total = chosen
            picks.append(Pick(c, needed, qty, up, total))
    # Compare on the cost of covering the requirement, not on the spares we add later.
    picks.sort(key=lambda p: (_base_cost(p), -p.candidate.stock, p.unit_price))
    return picks, note


def _base_cost(p: Pick) -> float:
    c = p.candidate
    minimum = max(p.needed, c.moq, min(b for b, _ in c.price_breaks))
    options = [minimum] + [b for b, _ in c.price_breaks if b >= minimum and b <= c.stock]
    return min(line_cost(c.price_breaks, q) for q in options)
