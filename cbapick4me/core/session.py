"""One user's working state: BOM, settings, results. Shared by every front end.

The web server creates one Session per browser tab; nothing here is global
except the search cache, which is intentionally shared.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .bom import Bom, load_bom, parse_bom
from .cache import SearchCache
from .config import Config, load_config
from . import padding
from .padding import PadOutcome
from .parse import Kind
from .picker import (
    ExtraResult,
    LineResult,
    Priced,
    ProgressFn,
    lookup_others,
    pick,
    render_output,
    substitute_value,
    total_cost,
)
from .specs import Line, Settings, build_lines
from .suppliers import Supplier, SupplierError, make_supplier

_cache: SearchCache | None = None
_cache_lock = threading.Lock()


def shared_cache(cfg: Config) -> SearchCache:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = SearchCache(cfg.cache_path)
        return _cache


class Session:
    def __init__(self, cfg: Config | None = None, settings: Settings | None = None):
        self.cfg = cfg or load_config()
        self.settings = settings or Settings()
        self.bom: Bom | None = None
        self.source_path: Path | None = None
        self.results: list[LineResult] = []
        # Non-C/R rows priced by MPN; only filled when basket padding is on.
        self.extras: list[ExtraResult] = []
        self._supplier: Supplier | None = None
        self._supplier_sig: tuple | None = None

    # --- BOM -----------------------------------------------------------------

    def load_path(self, path: str | Path) -> Bom:
        self.source_path = Path(path)
        self.bom = load_bom(path)
        self._reset()
        return self.bom

    def load_bytes(self, data: bytes, name: str) -> Bom:
        self.source_path = None
        self.bom = parse_bom(data, name)
        self._reset()
        return self.bom

    def _reset(self) -> None:
        self.results = []
        self.extras = []
        self.settings.overrides.clear()

    def lines(self) -> list[Line]:
        return build_lines(self.bom, self.settings) if self.bom else []

    def designator_rows(self) -> list[dict]:
        """One entry per C/R designator with its effective value/spec, for review tables."""
        out = []
        if not self.bom:
            return out
        for row in self.bom.pickable_rows:
            for d in row.designators:
                spec = self.settings.effective_spec(d, row.kind)
                out.append(
                    {
                        "designator": d,
                        "kind": row.kind,
                        "value_text": row.value_text,
                        "value": self.settings.effective_value(d, row),
                        "footprint": row.footprint,
                        "size": row.size,
                        "spec": spec,
                        "overridden": self.settings.is_overridden(d),
                        "row": row,
                    }
                )
        return out

    def kind_of(self, designator: str) -> Kind | None:
        if self.bom:
            for row in self.bom.pickable_rows:
                if designator in row.designators:
                    return row.kind
        return None

    def apply_exception(self, designators: list[str], changes: dict[str, object]) -> None:
        """Apply the same override to several designators, which must all be one type."""
        kinds = {self.kind_of(d) for d in designators}
        if None in kinds:
            unknown = [d for d in designators if self.kind_of(d) is None]
            raise ValueError(f"Not a capacitor/resistor in this BOM: {', '.join(unknown)}")
        if len(kinds) > 1:
            raise ValueError("Selection mixes capacitors and resistors — edit each type separately")
        for d in designators:
            self.settings.set_override(d, **changes)

    # --- supplier --------------------------------------------------------------

    def supplier(self) -> Supplier:
        sig = (
            self.cfg.digikey_client_id,
            self.cfg.digikey_client_secret,
            self.cfg.digikey_site,
            self.cfg.digikey_sandbox,
            self.cfg.effective_currency,
        )
        if self._supplier is None or sig != self._supplier_sig:
            self._supplier = make_supplier(self.cfg, shared_cache(self.cfg))
            self._supplier_sig = sig
        return self._supplier

    def check_ready(self) -> str | None:
        """Return a user-facing problem that prevents picking, or None."""
        if not self.bom:
            return "Load a BOM first"
        if not self.bom.pickable_rows:
            return "No capacitors or resistors found in this BOM"
        if not self.cfg.has_keys():
            return "No DigiKey API credentials — add them under API keys"
        return None

    # --- picking ---------------------------------------------------------------

    def run(self, on_progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> list[LineResult]:
        problem = self.check_ready()
        if problem:
            raise SupplierError(problem)
        supplier = self.supplier()
        results = pick(self.bom, self.settings, supplier, on_progress=on_progress, cancel=cancel)
        extras = []
        if self.settings.pad_enabled:
            extras = lookup_others(self.bom, self.settings, supplier, on_progress=on_progress, cancel=cancel)
        self.results, self.extras = results, extras
        return self.results

    def select(self, result_index: int, pick_index: int) -> None:
        r = self.results[result_index]
        if 0 <= pick_index < len(r.picks):
            r.selected = pick_index
            r.pad = None

    # --- basket padding --------------------------------------------------------

    def basket(self) -> list[Priced]:
        """Every priced (or unpriced) line, C/R results and other rows, in BOM order."""
        items: list[Priced] = [*self.results, *self.extras]
        return sorted(items, key=lambda x: x.row.index)  # stable: split C/R lines keep their order

    def clear_padding(self) -> None:
        for x in self.basket():
            x.pad = None

    def pad(self, indices: list[int]) -> PadOutcome:
        """Top up the basket lines at these indices (into basket()) to reach the threshold."""
        self.clear_padding()
        basket = self.basket()
        chosen = [basket[i] for i in indices if basket[i].base_pick]
        outcome = padding.pad([x.base_pick for x in chosen], self.settings.pad_threshold, self.total)
        for x, p in zip(chosen, outcome.picks):
            x.pad = p if p.padded else None
        return outcome

    @property
    def shortfall(self) -> float:
        return round(max(0.0, self.settings.pad_threshold - self.total), 2)

    @property
    def unpriced_count(self) -> int:
        return sum(1 for x in self.basket() if not x.ok)

    def accept_suggestion(self, result_index: int, value: float) -> None:
        substitute_value(self.settings, self.results[result_index].line, value)

    def accept_all_nearest(self) -> bool:
        changed = False
        for r in self.results:
            if not r.ok and r.suggestions and r.line.kind is Kind.RESISTOR:
                substitute_value(self.settings, r.line, r.suggestions[0])
                changed = True
        return changed

    def test_keys(self) -> str:
        """Check the DigiKey credentials with one request. Raises SupplierError on failure."""
        if not self.cfg.has_keys():
            raise SupplierError("No DigiKey API credentials entered")
        return make_supplier(self.cfg).check()

    @property
    def currency(self) -> str:
        """Currency of the prices shown: what the supplier returned, else the configured one."""
        for r in self.basket():
            if r.pick and r.pick.candidate.currency:
                return r.pick.candidate.currency
        return self.cfg.effective_currency

    @property
    def total(self) -> float:
        return total_cost(self.basket())

    # --- output ----------------------------------------------------------------

    def output_name(self) -> str:
        return self.bom.output_name() if self.bom else "bom_picked4u.csv"

    def output_bytes(self) -> bytes:
        return render_output(self.bom, self.results, self.settings, self.extras)

    def save(self, path: str | Path | None = None) -> Path:
        if path is None:
            folder = self.source_path.parent if self.source_path else Path.cwd()
            path = folder / self.output_name()
        path = Path(path)
        path.write_bytes(self.output_bytes())
        return path
