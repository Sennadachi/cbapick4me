pytest_plugins = ["nicegui.testing.user_plugin"]

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def wayfinder_bytes() -> bytes:
    return (FIXTURES / "wayfinder.csv").read_bytes()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never read the developer's real config, .env or API keys during tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # XDG_CONFIG_HOME only counts on Linux; pin the config dir on Windows/macOS too.
    import cbapick4me.core.config as config
    import cbapick4me.core.formats as formats

    for mod in (config, formats):
        monkeypatch.setattr(mod, "user_config_dir", lambda *a, **k: str(tmp_path / "config" / "cbapick4me"))
    monkeypatch.setenv("CBAPICK_CACHE_DIR", str(tmp_path / "cache"))
    for var in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBAPICK_THEME", "off")
    monkeypatch.chdir(tmp_path)
    import cbapick4me.core.session as session

    monkeypatch.setattr(session, "_cache", None)


@pytest.fixture
def fake_supplier(monkeypatch):
    """Stand in for DigiKey: offline synthetic parts, and keys count as entered."""
    import cbapick4me.core.session as session
    from cbapick4me.core.config import Config

    from .fake_supplier import FakeSupplier

    monkeypatch.setattr(session, "make_supplier", lambda cfg, cache=None: FakeSupplier())
    monkeypatch.setattr(Config, "has_keys", lambda self: True)
