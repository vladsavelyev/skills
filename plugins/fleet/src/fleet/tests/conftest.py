import pytest

from fleet import sync


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep the developer's real ~/.config/fleet/config.toml out of every test."""
    monkeypatch.setattr(sync, "CONFIG_PATH", tmp_path / "fleet" / "config.toml")
