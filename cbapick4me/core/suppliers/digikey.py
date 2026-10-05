"""DigiKey Product Information API v4 (2-legged OAuth, client credentials)."""

from __future__ import annotations

import threading
import time

import requests

from ..cache import SearchCache
from ..match import attr_dielectric, attr_power, attr_size, attr_tolerance, attr_value, attr_voltage
from ..parse import Kind, keyword_values
from ..specs import SearchKey
from .base import Candidate, SupplierError, exact_mpn, search_queries
from .http import Throttle, request_json

PROD = "https://api.digikey.com"
SANDBOX = "https://sandbox-api.digikey.com"
PAGE = 50
MAX_PAGES = 3

# DigiKey only sells Digi-Reel with a reeling fee; never pick it automatically.
EXCLUDED_PACKAGING = ("digi-reel",)


class DigiKey:
    name = "DigiKey"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        site: str = "US",
        currency: str = "USD",
        sandbox: bool = False,
        cache: SearchCache | None = None,
    ):
        if not client_id or not client_secret:
            raise SupplierError("DigiKey client ID and secret are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.site = site
        self.currency = currency
        self.base = SANDBOX if sandbox else PROD
        self.cache = cache
        self.session = requests.Session()
        self.throttle = Throttle(per_minute=100)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    # --- auth --------------------------------------------------------------

    def _access_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expiry - 30:
                return self._token
            data = request_json(
                self.session,
                "POST",
                f"{self.base}/v1/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
            )
            self._token = data["access_token"]
            self._token_expiry = time.time() + float(data.get("expires_in", 600))
            return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "X-DIGIKEY-Locale-Site": self.site,
            "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": self.currency,
            "Content-Type": "application/json",
        }

    # --- search ------------------------------------------------------------

    def describe_query(self, key: SearchKey) -> str:
        return " | ".join(build_queries(key))

    def check(self) -> str:
        self._token = None
        self._access_token()
        return f"DigiKey keys OK (site {self.site}, prices in {self.currency})"

    def _fetch_page(self, keywords: str, offset: int) -> dict:
        body = {
            "Keywords": keywords,
            "Limit": PAGE,
            "Offset": offset,
            "FilterOptionsRequest": {
                "SearchOptions": ["InStock"],
                "MarketPlaceFilter": "ExcludeMarketPlace",
            },
            "SortOptions": {"Field": "Price", "SortOrder": "Ascending"},
        }

        def fetch() -> dict:
            return request_json(
                self.session,
                "POST",
                f"{self.base}/products/v4/search/keyword",
                headers=self._headers(),
                json=body,
                throttle=self.throttle,
            )

        if self.cache is None:
            return fetch()
        key = SearchCache.make_key("digikey", self.base, self.site, self.currency, body)
        return self.cache.get_or_fetch(key, fetch)

    def search(self, key: SearchKey) -> list[Candidate]:
        def fetch(query: str, page: int) -> tuple[list[Candidate], int, int]:
            data = self._fetch_page(query, page * PAGE)
            products = data.get("Products") or []
            cands = [c for p in products for c in parse_product(p, key.kind, self.currency)]
            return cands, len(products), int(data.get("ProductsCount") or 0)

        return search_queries(build_queries(key), fetch, key, page_size=PAGE, max_pages=MAX_PAGES)

    def lookup(self, mpn: str, supplier_part: str = "") -> list[Candidate]:
        for keywords in dict.fromkeys(k for k in (supplier_part, mpn) if k):
            products = self._fetch_page(keywords, 0).get("Products") or []
            cands = [c for p in products for c in parse_product(p, Kind.OTHER, self.currency)]
            hits = exact_mpn(cands, mpn, supplier_part)
            if hits:
                return hits
        return []


def _params(p: dict) -> dict[str, str]:
    return {
        (x.get("ParameterText") or "").strip(): (x.get("ValueText") or "").strip()
        for x in p.get("Parameters") or []
    }


def parse_product(p: dict, kind: Kind, currency: str = "") -> list[Candidate]:
    """One Candidate per orderable packaging variation of a DigiKey product."""
    params = _params(p)
    desc = (p.get("Description") or {}).get("ProductDescription", "") or ""
    status = ((p.get("ProductStatus") or {}).get("Status") or "Active").lower()
    if status not in ("active",):
        return []

    # Parts looked up by MPN (Kind.OTHER) have no spec attributes to check.
    attrs: dict[str, object] = {}
    if kind is Kind.CAPACITOR:
        attrs["value"] = attr_value(params.get("Capacitance", ""), kind)
        attrs["voltage"] = attr_voltage(params.get("Voltage - Rated", ""))
        attrs["dielectric"] = attr_dielectric(params.get("Temperature Coefficient", ""))
        attrs["family_ok"] = desc.upper().startswith("CAP CER")
    elif kind is Kind.RESISTOR:
        attrs["value"] = attr_value(params.get("Resistance", ""), kind)
        attrs["power"] = attr_power(params.get("Power (Watts)", ""))
        attrs["family_ok"] = desc.upper().startswith("RES ") and "ARRAY" not in desc.upper()
    if kind is not Kind.OTHER:
        attrs["tolerance"] = attr_tolerance(params.get("Tolerance", ""))
        attrs["size"] = attr_size(params.get("Package / Case", "") or params.get("Supplier Device Package", ""))

    mfr = (p.get("Manufacturer") or {}).get("Name", "")
    mpn = p.get("ManufacturerProductNumber", "")
    url = p.get("ProductUrl", "")

    cands = []
    for var in p.get("ProductVariations") or []:
        pkg = ((var.get("PackageType") or {}).get("Name") or "").strip()
        if any(x in pkg.lower() for x in EXCLUDED_PACKAGING):
            continue
        breaks = [
            (int(b["BreakQuantity"]), float(b["UnitPrice"]))
            for b in var.get("StandardPricing") or []
            if b.get("BreakQuantity") and b.get("UnitPrice") is not None
        ]
        stock = int(var.get("QuantityAvailableforPackageType") or 0)
        if not breaks or stock <= 0:
            continue
        cands.append(
            Candidate(
                mpn=mpn,
                manufacturer=mfr,
                supplier="DigiKey",
                supplier_part=var.get("DigiKeyProductNumber", ""),
                description=desc,
                stock=stock,
                price_breaks=sorted(breaks),
                moq=int(var.get("MinimumOrderQuantity") or 1),
                packaging=pkg,
                url=url,
                currency=currency,
                attrs=dict(attrs),
            )
        )
    return cands


def build_queries(key: SearchKey) -> list[str]:
    out = []
    for v in keyword_values(key.value, key.kind):
        if key.kind is Kind.CAPACITOR:
            parts = ["CAP CER", v, key.size]
            if key.spec.dielectric:
                parts.append(key.spec.dielectric)
        else:
            parts = ["RES", v, key.size]
            if key.spec.tolerance:
                parts.append(f"{key.spec.tolerance:g}%")
        out.append(" ".join(parts))
    return out
