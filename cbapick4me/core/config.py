"""API credentials and remembered defaults.

Lookup order (later wins): config file → .env in the working directory →
environment variables. The config file lives in the platform config dir
(~/.config/cbapick4me/config.toml, %APPDATA%\\cbapick4me\\config.toml).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

APP = "cbapick4me"

# DigiKey locale sites and their native currency.
SITE_CURRENCY = {
    "US": "USD", "CA": "CAD", "UK": "GBP", "IE": "EUR", "DE": "EUR", "FR": "EUR", "IT": "EUR",
    "ES": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR", "FI": "EUR", "SE": "SEK", "DK": "DKK",
    "NO": "NOK", "CH": "CHF", "PL": "PLN", "AU": "AUD", "NZ": "NZD", "JP": "JPY", "CN": "CNY",
    "SG": "SGD", "HK": "HKD", "IN": "INR", "MX": "MXN", "BR": "BRL",
}

ENV_NAMES = {
    "digikey_client_id": "DIGIKEY_CLIENT_ID",
    "digikey_client_secret": "DIGIKEY_CLIENT_SECRET",
    "digikey_site": "DIGIKEY_SITE",
    "digikey_sandbox": "DIGIKEY_SANDBOX",
    "currency": "CBAPICK_CURRENCY",
    "cache_dir": "CBAPICK_CACHE_DIR",
}


@dataclass
class Config:
    digikey_client_id: str = ""
    digikey_client_secret: str = ""
    digikey_site: str = "US"
    digikey_sandbox: bool = False
    currency: str = ""  # blank = the DigiKey site's own currency
    cache_dir: str = ""

    @property
    def effective_currency(self) -> str:
        return (self.currency or SITE_CURRENCY.get(self.digikey_site.upper(), "USD")).upper()

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir) if self.cache_dir else Path(user_cache_dir(APP))

    def has_keys(self) -> bool:
        return bool(self.digikey_client_id and self.digikey_client_secret)


def config_path() -> Path:
    return Path(user_config_dir(APP, appauthor=False)) / "config.toml"


def _read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def _coerce(name: str, raw: object) -> object:
    if name == "digikey_sandbox":
        return str(raw).lower() in ("1", "true", "yes", "on")
    return str(raw)


def load_config(*, use_file: bool = True, use_env: bool = True) -> Config:
    cfg = Config()
    names = {f.name for f in fields(Config)}
    if use_file:
        try:
            data = tomllib.loads(config_path().read_text("utf-8"))
            for k, v in data.items():
                if k in names:
                    setattr(cfg, k, _coerce(k, v))
        except (OSError, tomllib.TOMLDecodeError):
            pass
    if use_env:
        env = {**_read_dotenv(Path.cwd() / ".env"), **os.environ}
        for attr, var in ENV_NAMES.items():
            if env.get(var):
                setattr(cfg, attr, _coerce(attr, env[var]))
    return cfg


def save_config(cfg: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in asdict(cfg).items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        else:
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
    path.write_text("\n".join(lines) + "\n", "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
