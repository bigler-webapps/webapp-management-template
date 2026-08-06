"""Unit tests for ansible/roles/server_health/files/responder.py.

Exercises evaluate_metric() — the pure staleness/breach decision — directly,
without spinning up a real HTTP server or touching a real filesystem beyond
the tmp_path status file the fixtures write.
"""
from __future__ import annotations

import json

import responder  # noqa: E402 — path set up by conftest.py

PROBE_INTERVAL = 60
MAX_AGE = 2 * PROBE_INTERVAL + 30  # matches make_handler's formula


def healthy_status():
    return {
        "disk": {"value_pct": 42.0, "healthy": True},
        "mem": {"value_pct": 55.0, "healthy": True},
        "swap": {"value_pct": 1.0, "healthy": True},
    }


def breached_status():
    return {
        "disk": {"value_pct": 95.0, "healthy": False},
        "mem": {"value_pct": 55.0, "healthy": True},
        "swap": {"value_pct": 1.0, "healthy": True},
    }


def test_evaluate_metric_healthy_and_fresh_returns_200():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(
        healthy_status(), "disk", now - 10, now, MAX_AGE
    )
    assert code == 200
    assert payload["healthy"] is True
    assert payload["value_pct"] == 42.0


def test_evaluate_metric_breached_returns_503():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(
        breached_status(), "disk", now - 10, now, MAX_AGE
    )
    assert code == 503
    assert payload["healthy"] is False
    assert payload["reason"] == "threshold breached"


def test_evaluate_metric_missing_status_never_looks_healthy():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(None, "disk", None, now, MAX_AGE)
    assert code == 503
    assert payload["healthy"] is False
    assert payload["value_pct"] is None


def test_evaluate_metric_stale_status_is_down_even_if_last_value_was_healthy():
    now = 1_000_000.0
    stale_mtime = now - (MAX_AGE + 1)  # just past the staleness window
    code, payload = responder.evaluate_metric(
        healthy_status(), "disk", stale_mtime, now, MAX_AGE
    )
    assert code == 503
    assert "stale" in payload["reason"]


def test_evaluate_metric_just_within_freshness_window_is_healthy():
    now = 1_000_000.0
    fresh_mtime = now - (MAX_AGE - 1)
    code, payload = responder.evaluate_metric(
        healthy_status(), "disk", fresh_mtime, now, MAX_AGE
    )
    assert code == 200


def test_evaluate_metric_unknown_metric_returns_503():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(
        healthy_status(), "nonexistent", now - 5, now, MAX_AGE
    )
    assert code == 503
    assert "unknown metric" in payload["reason"]


def test_load_status_missing_file_returns_none(tmp_path):
    assert responder.load_status(tmp_path / "does-not-exist.json") is None


def test_load_status_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("{not valid json")
    assert responder.load_status(path) is None


def test_load_status_valid_file(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(healthy_status()))
    loaded = responder.load_status(path)
    assert loaded["disk"]["healthy"] is True


# --- display-only metric ---------------------------------------------------

def display_only_status():
    return {
        "disk": {"value_pct": 42.0, "healthy": True},
        "mem": {"value_pct": 55.0, "healthy": True},
        "swap": {"value_pct": 95.0, "healthy": True, "display_only": True},
    }


def test_evaluate_metric_display_only_healthy_returns_200_with_distinct_reason():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(
        display_only_status(), "swap", now - 10, now, MAX_AGE
    )
    assert code == 200
    assert payload["healthy"] is True
    assert payload["value_pct"] == 95.0
    assert payload["reason"] != "ok"
    assert "display-only" in payload["reason"]


def test_evaluate_metric_display_only_stale_status_still_returns_503():
    # Display-only disables the *threshold*, never the freshness contract
    # ("no data" must never look healthy).
    now = 1_000_000.0
    stale_mtime = now - (MAX_AGE + 1)
    code, payload = responder.evaluate_metric(
        display_only_status(), "swap", stale_mtime, now, MAX_AGE
    )
    assert code == 503
    assert "stale" in payload["reason"]


def test_evaluate_metric_display_only_missing_status_still_returns_503():
    now = 1_000_000.0
    code, payload = responder.evaluate_metric(None, "swap", None, now, MAX_AGE)
    assert code == 503
    assert payload["healthy"] is False
