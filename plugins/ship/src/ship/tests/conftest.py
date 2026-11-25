import pytest

from ship import hold


@pytest.fixture(autouse=True)
def _no_real_holds(monkeypatch, tmp_path_factory):
    """Run every test outside a Claude session, with holds in a scratch dir. Inside one, a test
    calling `problem resolve` held its fake branch for the real session running the suite (#137),
    and the Stop hook then failed on it."""
    monkeypatch.delenv(hold.SESSION_ENV, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
