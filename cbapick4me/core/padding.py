"""Basket padding: raise order quantities on chosen lines to reach a free-shipping threshold.

The shortfall is shared as equal extra spend per line. Lines are topped up one unit at a
time, always the line that has had the least extra spend so far. After each step a line
jumps to the largest quantity its spend can buy, so a cheaper price break is used
automatically. Lines that run out of stock stop, and the others take up the rest. The last
step goes to whichever line closes the gap with the least overshoot.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, replace

from .pricing import Pick, line_cost, max_qty_within, unit_price

REACHED = "reached"
ALREADY_MET = "already_met"
UNREACHABLE = "unreachable"


@dataclass
class PadOutcome:
    picks: list[Pick]  # one per input pick, with order_qty raised (padded = extra units)
    total: float  # basket total after padding
    status: str
    added: float = 0.0  # extra spend on the padded lines


def _grow(p: Pick, qty: int) -> tuple[int, float]:
    """Next quantity above qty for this pick, and its line cost, using the best break for that spend."""
    breaks, stock = p.candidate.price_breaks, p.candidate.stock
    cost = line_cost(breaks, qty + 1)
    qty = max_qty_within(breaks, cost, at_least=qty + 1, stock=stock)
    return qty, line_cost(breaks, qty)


def _finish(p: Pick, qty: int, target: float) -> tuple[int, float] | None:
    """Smallest growth of this pick whose line cost reaches target, or None if stock runs out."""
    cost = line_cost(p.candidate.price_breaks, qty)
    while cost < target - 1e-9:
        if qty >= p.candidate.stock:
            return None
        qty, cost = _grow(p, qty)
    return qty, cost


def pad(picks: list[Pick], threshold: float, current_total: float) -> PadOutcome:
    """Pad the given picks so the basket (current_total includes them) reaches threshold."""
    if current_total >= threshold - 1e-9:
        return PadOutcome(list(picks), round(current_total, 2), ALREADY_MET)
    start = current_total

    qty = [p.order_qty for p in picks]
    cost = [p.line_total for p in picks]
    total = current_total
    # Heap of (extra spend so far, index); lines out of stock are dropped.
    heap = [(0.0, i) for i, p in enumerate(picks) if p.candidate.stock > p.order_qty]
    heapq.heapify(heap)
    while total < threshold - 1e-9 and heap:
        _, i = heapq.heappop(heap)
        step = _grow(picks[i], qty[i])
        if total + step[1] - cost[i] >= threshold - 1e-9:
            # Final step (the loop ends after it): finish on whichever line closes the
            # gap with the least overshoot, so one dear part doesn't overshoot much.
            gap = threshold - total
            options = [(i, step)] + [(j, _finish(picks[j], qty[j], cost[j] + gap)) for _, j in heap]
            i, step = min(((j, st) for j, st in options if st), key=lambda o: o[1][1] - cost[o[0]])
        new_qty, new_cost = step
        total += new_cost - cost[i]
        qty[i], cost[i] = new_qty, new_cost
        if new_qty < picks[i].candidate.stock:
            heapq.heappush(heap, (new_cost - picks[i].line_total, i))

    out = []
    for p, q in zip(picks, qty):
        if q == p.order_qty:
            out.append(p)
            continue
        breaks = p.candidate.price_breaks
        out.append(
            replace(p, order_qty=q, unit_price=unit_price(breaks, q), line_total=line_cost(breaks, q), padded=p.padded + q - p.order_qty)
        )
    status = REACHED if total >= threshold - 1e-9 else UNREACHABLE
    return PadOutcome(out, round(total, 2), status, round(total - start, 2))
