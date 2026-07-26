import pytest

from test_personalization import _dispatcher_probe_timeout_seconds


def test_dispatcher_probe_timeout_covers_two_configured_poll_intervals(monkeypatch):
    monkeypatch.setenv("JOB_POLL_INTERVAL_SECONDS", "5")

    assert _dispatcher_probe_timeout_seconds() == pytest.approx(11.0)


@pytest.mark.parametrize("value", ["invalid", "-1"])
def test_dispatcher_probe_timeout_never_drops_below_default_window(monkeypatch, value):
    monkeypatch.setenv("JOB_POLL_INTERVAL_SECONDS", value)

    assert _dispatcher_probe_timeout_seconds() == pytest.approx(7.0)
