"""Unit tests for ansible/roles/server_health/files/probe.py.

Exercises the pure logic (threshold comparison, sustained-breach counter,
/proc/meminfo + statvfs parsing) against injected fixtures — no real
filesystem/proc access.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import probe  # noqa: E402 — path set up by conftest.py


def make_meminfo(mem_total_kb, mem_available_kb, swap_total_kb=0, swap_free_kb=0):
    return (
        f"MemTotal:       {mem_total_kb} kB\n"
        f"MemFree:        1000 kB\n"
        f"MemAvailable:   {mem_available_kb} kB\n"
        f"SwapTotal:      {swap_total_kb} kB\n"
        f"SwapFree:       {swap_free_kb} kB\n"
    )


def make_statvfs(f_blocks, f_bfree):
    return SimpleNamespace(f_blocks=f_blocks, f_bfree=f_bfree)


BASE_CONFIG = {
    "disk_threshold_pct": 90,
    "disk_mounts": ["/"],
    "mem_available_min_pct": 10,
    "mem_sustained_samples": 3,
    "swap_max_pct": 50,
    "swap_sustained_samples": 3,
    "state_dir": "/unused-in-tests",
}


def config(tmp_path, **overrides):
    cfg = dict(BASE_CONFIG)
    cfg["state_dir"] = str(tmp_path)
    cfg.update(overrides)
    return cfg


# --- pure parsing / math -----------------------------------------------------

def test_parse_meminfo_ok():
    meminfo = probe.parse_meminfo(make_meminfo(8_000_000, 4_000_000, 2_000_000, 1_000_000))
    assert meminfo["MemTotal"] == 8_000_000
    assert meminfo["MemAvailable"] == 4_000_000


def test_parse_meminfo_unparseable_raises():
    with pytest.raises(probe.ProbeError):
        probe.parse_meminfo("this is not meminfo at all\nno colons here")


def test_parse_meminfo_missing_keys_raises():
    with pytest.raises(probe.ProbeError):
        probe.parse_meminfo("MemTotal: 1000 kB\n")  # missing MemAvailable etc.


def test_mem_available_pct():
    meminfo = probe.parse_meminfo(make_meminfo(8_000_000, 800_000))
    assert probe.mem_available_pct(meminfo) == pytest.approx(10.0)


def test_swap_used_pct_no_swap_configured():
    meminfo = probe.parse_meminfo(make_meminfo(8_000_000, 4_000_000, swap_total_kb=0, swap_free_kb=0))
    assert probe.swap_used_pct(meminfo) == 0.0


def test_swap_used_pct_partial_usage():
    meminfo = probe.parse_meminfo(
        make_meminfo(8_000_000, 4_000_000, swap_total_kb=1_000_000, swap_free_kb=400_000)
    )
    assert probe.swap_used_pct(meminfo) == pytest.approx(60.0)


def test_disk_used_pct():
    assert probe.disk_used_pct(f_blocks=1000, f_bfree=100) == pytest.approx(90.0)


def test_disk_used_pct_zero_blocks_raises():
    with pytest.raises(probe.ProbeError):
        probe.disk_used_pct(f_blocks=0, f_bfree=0)


# --- sustained-breach counter -------------------------------------------------

def test_sustained_breach_tracker_requires_n_consecutive_samples():
    tracker = probe.SustainedBreachTracker()
    # A one-sample spike (fewer than N=3) must NOT report a breach.
    assert tracker.update("mem", True, required_samples=3) is False
    assert tracker.update("mem", False, required_samples=3) is False  # spike recovers
    assert tracker.update("mem", True, required_samples=3) is False
    assert tracker.update("mem", True, required_samples=3) is False
    # Third CONSECUTIVE breach -> now sustained.
    assert tracker.update("mem", True, required_samples=3) is True


def test_sustained_breach_tracker_disk_fires_on_one_sample():
    tracker = probe.SustainedBreachTracker()
    assert tracker.update("disk", True, required_samples=1) is True


def test_sustained_breach_tracker_persists_across_load_save(tmp_path):
    state_path = tmp_path / "breach_counts.json"
    tracker = probe.SustainedBreachTracker()
    tracker.update("mem", True, required_samples=3)
    tracker.update("mem", True, required_samples=3)
    tracker.save(state_path)

    reloaded = probe.SustainedBreachTracker.load(state_path)
    assert reloaded.update("mem", True, required_samples=3) is True  # third consecutive


def test_sustained_breach_tracker_load_corrupt_state_starts_clean(tmp_path):
    state_path = tmp_path / "breach_counts.json"
    state_path.write_text("{not json")
    tracker = probe.SustainedBreachTracker.load(state_path)
    assert tracker.counts == {}


# --- build_status (integration of the pure pieces) ---------------------------

def test_build_status_full_disk_breaches_on_single_sample(tmp_path):
    cfg = config(tmp_path)
    status = probe.build_status(
        cfg,
        make_meminfo(8_000_000, 4_000_000),  # healthy memory
        {"/": make_statvfs(f_blocks=1000, f_bfree=20)},  # 98% used > 90% threshold
    )
    assert status["disk"]["healthy"] is False
    assert status["mem"]["healthy"] is True


def test_build_status_memory_spike_shorter_than_n_does_not_alert(tmp_path):
    cfg = config(tmp_path, mem_sustained_samples=3)
    disk_ok = {"/": make_statvfs(f_blocks=1000, f_bfree=500)}  # 50% used, healthy

    # Two breaching samples (< 3 required) — must stay healthy.
    for _ in range(2):
        status = probe.build_status(cfg, make_meminfo(8_000_000, 400_000), disk_ok)  # 5% avail < 10%
        assert status["mem"]["healthy"] is True


def test_build_status_sustained_memory_breach_alerts(tmp_path):
    cfg = config(tmp_path, mem_sustained_samples=3)
    disk_ok = {"/": make_statvfs(f_blocks=1000, f_bfree=500)}

    status = None
    for _ in range(3):
        status = probe.build_status(cfg, make_meminfo(8_000_000, 400_000), disk_ok)  # 5% avail < 10%
    assert status["mem"]["healthy"] is False


def test_build_status_unparseable_meminfo_raises_and_does_not_write_status(tmp_path):
    cfg = config(tmp_path)
    disk_ok = {"/": make_statvfs(f_blocks=1000, f_bfree=500)}
    with pytest.raises(probe.ProbeError):
        probe.build_status(cfg, "garbage, not meminfo", disk_ok)
    # No status.json must exist after a failed build_status call.
    assert not (tmp_path / "status.json").exists()


# --- swap display-only ------------------------------------------------------

def test_build_status_swap_display_only_never_breaches_even_at_high_usage(tmp_path):
    cfg = config(tmp_path, swap_max_pct=None)
    disk_ok = {"/": make_statvfs(f_blocks=1000, f_bfree=500)}  # healthy

    status = probe.build_status(
        cfg,
        make_meminfo(8_000_000, 4_000_000, swap_total_kb=1_000_000, swap_free_kb=50_000),  # 95% swap
        disk_ok,
    )
    assert status["swap"]["value_pct"] == pytest.approx(95.0)
    assert status["swap"]["healthy"] is True
    assert status["swap"]["display_only"] is True


def test_build_status_swap_display_only_does_not_affect_disk_or_mem(tmp_path):
    cfg = config(tmp_path, swap_max_pct=None)
    # Disk breaching, mem healthy, swap at 95% (would breach at the old default of 50%).
    status = probe.build_status(
        cfg,
        make_meminfo(8_000_000, 4_000_000, swap_total_kb=1_000_000, swap_free_kb=50_000),
        {"/": make_statvfs(f_blocks=1000, f_bfree=20)},  # 98% used > 90% threshold
    )
    assert status["disk"]["healthy"] is False
    assert status["mem"]["healthy"] is True
    assert status["swap"]["healthy"] is True


def test_build_status_swap_threshold_still_enforced_when_not_none(tmp_path):
    # Regression guard: swap_max_pct is still a real threshold when a caller sets one
    # explicitly (e.g. a future re-enable) -- display-only is opt-in via None, not the
    # only code path left standing.
    cfg = config(tmp_path, swap_max_pct=50)
    disk_ok = {"/": make_statvfs(f_blocks=1000, f_bfree=500)}
    status = None
    for _ in range(3):  # swap_sustained_samples default is 3
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000, swap_total_kb=1_000_000, swap_free_kb=400_000),  # 60%
            disk_ok,
        )
    assert status["swap"]["healthy"] is False
    assert "display_only" not in status["swap"]


def test_run_exits_nonzero_and_skips_status_write_on_missing_config(tmp_path):
    # run() reads /proc/meminfo + os.statvfs, which only exist on Linux (the
    # 6 target hosts) — this case fails before either is reached (bad config
    # path), so it stays portable and needs no injected OS fixtures.
    missing_config = tmp_path / "does-not-exist.json"
    rc = probe.run(config_path=str(missing_config))
    assert rc == 1
    assert not (tmp_path / "status.json").exists()
