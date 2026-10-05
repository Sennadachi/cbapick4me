from __future__ import annotations

from ..cache import SearchCache
from ..config import Config
from .base import Candidate, Supplier, SupplierAuthError, SupplierError


def make_supplier(cfg: Config, cache: SearchCache | None = None) -> Supplier:
    from .digikey import DigiKey

    return DigiKey(
        cfg.digikey_client_id,
        cfg.digikey_client_secret,
        site=cfg.digikey_site,
        currency=cfg.effective_currency,
        sandbox=cfg.digikey_sandbox,
        cache=cache,
    )


__all__ = ["Candidate", "Supplier", "SupplierAuthError", "SupplierError", "make_supplier"]
