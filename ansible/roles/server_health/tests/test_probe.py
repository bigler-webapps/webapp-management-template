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


def make_statvfs(f_blocks, f_bfree, f_files=10_000, f_ffree=9_000, f_frsize=1):
    return SimpleNamespace(
        f_blocks=f_blocks,
        f_bfree=f_bfree,
        f_files=f_files,
        f_ffree=f_ffree,
        f_frsize=f_frsize,
    )


BASE_CONFIG = {
    "disk_threshold_pct": 90,
    "disk_mounts": ["/"],
    "disk_trend_window_sec": 86_400,
    "disk_days_to_full_min": 3,
    "disk_trend_sustained_samples": 3,
    "disk_trend_floor_margin_pct": None,
    "growth_watch_dirs": [],
    "inode_threshold_pct": 90,
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


def test_inode_used_pct():
    assert probe.inode_used_pct(f_files=1_000, f_ffree=100) == pytest.approx(90.0)


def test_inode_used_pct_zero_files_raises():
    with pytest.raises(probe.ProbeError):
        probe.inode_used_pct(f_files=0, f_ffree=0)


def test_days_to_full_steady_fill_matches_arithmetic_exactly():
    # 1,000 bytes consumed in 100 seconds; 9,000 bytes remain => 900 seconds.
    samples = [(1_000.0, 10_000), (1_100.0, 9_000)]
    assert probe.days_to_full(samples) == pytest.approx(900 / 86_400)


def test_days_to_full_flat_or_shrinking_usage_has_no_projection():
    assert probe.days_to_full([(1_000.0, 10_000), (1_100.0, 10_000)]) is None
    assert probe.days_to_full([(1_000.0, 9_000), (1_100.0, 10_000)]) is None


def test_disk_history_prunes_all_mounts_by_time_not_sample_count():
    history = probe.DiskHistory({"/old": [(100.0, 10_000)]})
    history.add("/", timestamp=200.0, free_bytes=9_000, window_sec=30)
    assert history.samples == {"/": [(200.0, 9_000)]}


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


def test_build_status_trend_requires_sustained_breaches(tmp_path):
    cfg = config(tmp_path, disk_days_to_full_min=3, disk_trend_sustained_samples=3)
    # The second and later samples project exhaustion in 2 days, but a single
    # steep sample must not make /health/disk fail by itself.
    for index, free_blocks in enumerate((2_883, 2_882, 2_881, 2_880)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=10_000, f_bfree=free_blocks)},
            now=1_000_000 + index * 60,
        )
        if index < 3:
            assert status["disk"]["healthy"] is True
    assert status["disk"]["days_to_full"] == pytest.approx(2.0)
    assert status["disk"]["healthy"] is False


def test_build_status_disk_trend_with_ample_headroom_is_display_only(tmp_path):
    cfg = config(
        tmp_path,
        disk_trend_sustained_samples=3,
        disk_trend_floor_margin_pct=5,
    )
    # total = 10,000 * 10,000 = 100,000,000 bytes. Veto boundary (threshold
    # 90, margin 5) is free% >= 100-(90-5) = 15%, i.e. free_bytes >= 15,000,000.
    # ~28.8% free here is comfortably inside that -- ample headroom.
    for index, free_blocks in enumerate((2_883, 2_882, 2_881, 2_880)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=10_000, f_bfree=free_blocks, f_frsize=10_000)},
            now=1_000_000 + index * 60,
        )
        assert status["disk"]["healthy"] is True
    assert status["disk"]["days_to_full"] == pytest.approx(2.0)
    assert status["disk"]["trend_display_only"] is True
    assert status["disk"]["trend_breach"] is False


def test_build_status_disk_trend_breaches_below_headroom_floor(tmp_path):
    cfg = config(
        tmp_path,
        disk_trend_sustained_samples=3,
        disk_trend_floor_margin_pct=5,
    )
    # total = 10,000 bytes (frsize=1). Veto boundary is free_bytes >= 1,500.
    # ~12% free here (1,200-1,203) is BELOW that -- not vetoed, still an
    # early warning -- and also above the absolute threshold's own 10%-free
    # trip point, so this isolates the trend firing on its own.
    for index, free_blocks in enumerate((1_203, 1_202, 1_201, 1_200)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=10_000, f_bfree=free_blocks)},
            now=1_000_000 + index * 60,
        )
    assert status["disk"]["threshold_breach"] is False
    assert status["disk"]["healthy"] is False
    assert status["disk"]["trend_breach"] is True
    assert "trend_display_only" not in status["disk"]


def test_build_status_disk_trend_headroom_veto_is_per_mount(tmp_path):
    cfg = config(
        tmp_path,
        disk_trend_sustained_samples=3,
        disk_trend_floor_margin_pct=5,
    )
    # / has ample headroom (frsize=10,000, ~28.8% free -> vetoed); /data has
    # the same bad slope with only ~12% free -> not vetoed. The healthy
    # mount must not mask the genuinely low-headroom one.
    for index, free_blocks in enumerate((2_883, 2_882, 2_881, 2_880)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {
                "/": make_statvfs(f_blocks=10_000, f_bfree=free_blocks, f_frsize=10_000),
                "/data": make_statvfs(f_blocks=10_000, f_bfree=free_blocks - 1_680),
            },
            now=1_000_000 + index * 60,
        )
    assert status["disk"]["healthy"] is False
    assert status["disk"]["trend_breach"] is True


def test_margin_scales_with_disk_size_unlike_a_fixed_byte_floor(tmp_path):
    """A fixed byte floor tuned for one host's disk size can sit ABOVE
    another, much larger host's own absolute-threshold trip point, which
    would silently veto a genuine slow fill there forever -- neutering the
    trend on the larger host, exactly the "over-corrects into uselessness"
    risk a fixed floor risks. The percentage-point margin is derived
    per-mount from each host's OWN total size instead, so the SAME 12%-free
    reading -- inside the genuine early-warning band on any host -- still
    breaches whether the disk is small or large."""
    for total_gb in (157, 320):
        total_bytes = total_gb * 1_000_000_000
        cfg = config(
            tmp_path / f"{total_gb}gb",
            disk_trend_sustained_samples=1,
            disk_trend_floor_margin_pct=5,
        )
        # 12% free: above the absolute threshold's 10%-free trip point (not
        # yet a threshold breach), but below the margin's 15%-free veto
        # boundary -- a steep slope should still fire here on EITHER size.
        # A fixed floor sized for the smaller disk would have wrongly vetoed
        # this on the larger disk (12% of 320 GB is well above a floor tuned
        # for 157 GB).
        free_start = int(total_bytes * 0.12)
        step = int(total_bytes * 0.001)
        status = None
        for i in range(3):
            status = probe.build_status(
                cfg,
                make_meminfo(8_000_000, 4_000_000),
                {"/": make_statvfs(f_blocks=total_bytes, f_bfree=free_start - i * step)},
                now=1_000_000 + i * 60,
            )
        assert status["disk"]["threshold_breach"] is False, total_gb
        assert status["disk"]["trend_breach"] is True, total_gb


def test_day_window_dilutes_a_dense_burst_the_old_window_would_have_flagged(tmp_path):
    """The burst fixture uses realistic dense sampling (the real probe runs
    every 60s) so at least the old 1800s window still has enough retained
    samples to compute a genuinely alarming rate, rather than falling to
    `None` from sparsity -- which would prove nothing about burst survival.
    This feeds the SAME dense sample sequence through both the pre-change
    1800s window (proving it really would have gone critical) and the new
    86400s window (proving the identical data is now diluted)."""
    total_bytes = 1_000_000_000
    start_free = 900_000_000
    step_bytes = 1_200_000  # per 300s sample -- a steep, busy-afternoon rate
    samples = [(i * 300, start_free - i * step_bytes) for i in range(37)]  # 3h, 5-min cadence

    old_cfg = config(
        tmp_path / "old-window",
        disk_trend_window_sec=1800,
        disk_trend_sustained_samples=1,
        disk_trend_floor_margin_pct=None,
    )
    peak = None
    for offset, free in samples:
        peak = probe.build_status(
            old_cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=total_bytes, f_bfree=free)},
            now=1_000_000 + offset,
        )
    # The old 30-minute window, evaluated at the end of the dense burst,
    # really would have gone critical -- not an artifact of sparse sampling.
    assert peak["disk"]["days_to_full"] is not None
    assert peak["disk"]["days_to_full"] < 3
    assert peak["disk"]["healthy"] is False

    new_cfg = config(
        tmp_path / "new-window",
        disk_trend_sustained_samples=1,
        disk_trend_floor_margin_pct=5,
    )
    status = None
    for offset, free in samples:
        status = probe.build_status(
            new_cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=total_bytes, f_bfree=free)},
            now=1_000_000 + offset,
        )
    # The burst ends and free space goes flat for the rest of the day. Only
    # a few representative points are needed -- days_to_full reads just the
    # oldest/newest retained sample, not every point in between.
    burst_end_offset = samples[-1][0]
    burst_end_free = samples[-1][1]
    for hours in (6, 12, 18, 20):
        status = probe.build_status(
            new_cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=total_bytes, f_bfree=burst_end_free)},
            now=1_000_000 + burst_end_offset + hours * 3_600,
        )
    # Same underlying burst, diluted by the full day including the quiet
    # tail: the burst's own start sample is still just inside the 24h
    # retention window, so the whole-day average rate is far gentler.
    assert status["disk"]["days_to_full"] > 3
    assert status["disk"]["healthy"] is True


def test_build_status_flat_or_shrinking_disk_never_triggers_trend(tmp_path):
    cfg = config(tmp_path, disk_days_to_full_min=30, disk_trend_sustained_samples=1)
    for index, free_blocks in enumerate((500, 500, 600)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=1_000, f_bfree=free_blocks)},
            now=1_000_000 + index * 60,
        )
        assert status["disk"]["days_to_full"] is None
        assert status["disk"]["healthy"] is True


def test_top_growth_directories_handles_empty_single_and_sorted_entries(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert probe.top_growth_directories([str(empty)]) == []

    single = tmp_path / "single"
    only = single / "only"
    only.mkdir(parents=True)
    (only / "data").write_bytes(b"x" * 7)
    assert probe.top_growth_directories([str(single)]) == [
        {"path": str(only), "size_bytes": 7}
    ]

    multi = tmp_path / "multi"
    for name, size in (("small", 3), ("large", 11), ("middle", 7)):
        entry = multi / name
        entry.mkdir(parents=True)
        (entry / "data").write_bytes(b"x" * size)
    growth = probe.top_growth_directories([str(multi)])
    assert [item["path"] for item in growth] == [
        str(multi / "large"), str(multi / "middle"), str(multi / "small")
    ]
    assert [item["size_bytes"] for item in growth] == [11, 7, 3]


def test_missing_growth_watch_directory_degrades_to_empty_list(tmp_path):
    assert probe.top_growth_directories([str(tmp_path / "not-present")]) == []


def test_top_growth_directories_sums_content_nested_deeper_than_report_depth(tmp_path):
    """Regression: a real Docker layer's content lives at overlay2/<hash>/diff/...,
    three levels below /var/lib/docker -- one level past the OLD recursion bound,
    which silently reported near-zero for exactly this shape. The reported entry
    for the depth-2 directory itself must reflect its full nested content."""
    watch_root = tmp_path / "docker"
    layer_dir = watch_root / "overlay2" / "abc123"
    content_dir = layer_dir / "diff" / "usr" / "lib"
    content_dir.mkdir(parents=True)
    (content_dir / "big.so").write_bytes(b"x" * 500)

    growth = probe.top_growth_directories([str(watch_root)])

    layer_entry = next(item for item in growth if item["path"] == str(layer_dir))
    assert layer_entry["size_bytes"] == 500


def test_top_growth_directories_degrades_on_permission_error_instead_of_raising(tmp_path, monkeypatch):
    """Review finding: the probe runs as an unprivileged nologin user
    with no docker-group grant, and Docker's data root is conventionally
    root-only -- a PermissionError while scanning it is an expected
    environmental condition, not a bug. It must not propagate to run()'s
    fail-loud boundary and take down disk/mem/swap reporting too."""
    watch_root = tmp_path / "docker"
    readable = watch_root / "readable"
    readable.mkdir(parents=True)
    (readable / "data").write_bytes(b"x" * 9)

    real_scandir = probe.os.scandir

    def flaky_scandir(path):
        if str(path) == str(watch_root / "overlay2"):
            raise PermissionError(f"denied: {path}")
        return real_scandir(path)

    (watch_root / "overlay2").mkdir()
    monkeypatch.setattr(probe.os, "scandir", flaky_scandir)

    growth = probe.top_growth_directories([str(watch_root)])

    by_path = {item["path"]: item["size_bytes"] for item in growth}
    assert by_path[str(readable)] == 9
    assert by_path[str(watch_root / "overlay2")] == 0


def test_top_growth_directories_stops_once_the_time_budget_is_spent(tmp_path):
    """WO risk: 'bound the depth AND the runtime.' Depth alone doesn't cap
    wall-clock time against an unexpectedly large tree; the deadline must
    stop further descent rather than let one slow scan delay every
    subsequent status.json write indefinitely."""
    watch_root = tmp_path / "docker"
    nested = watch_root / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "data").write_bytes(b"x" * 42)

    calls = {"n": 0}

    def exhausted_after_first_check():
        calls["n"] += 1
        return 0.0 if calls["n"] <= 1 else 1_000_000.0  # budget spent immediately after entry

    growth = probe.top_growth_directories([str(watch_root)], clock=exhausted_after_first_check)

    # Deadline computed from the first clock() call; the very next check
    # (entering scan() for the root) already reads past it, so no entry --
    # not even the root's own children -- is ever scanned or reported.
    assert growth == []


def test_build_status_inode_exhaustion_is_unhealthy_with_ample_blocks(tmp_path):
    status = probe.build_status(
        config(tmp_path),
        make_meminfo(8_000_000, 4_000_000),
        {"/": make_statvfs(f_blocks=1_000, f_bfree=500, f_files=1_000, f_ffree=10)},
        now=1_000_000,
    )
    assert status["disk"]["healthy"] is True
    assert status["inode"]["healthy"] is False


def test_build_status_inode_healthy_when_blocks_are_exhausted(tmp_path):
    status = probe.build_status(
        config(tmp_path),
        make_meminfo(8_000_000, 4_000_000),
        {"/": make_statvfs(f_blocks=1_000, f_bfree=20, f_files=1_000, f_ffree=500)},
        now=1_000_000,
    )
    assert status["disk"]["healthy"] is False
    assert status["inode"]["healthy"] is True


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


# --- disk trend display-only --------------------------------------------------

def test_build_status_disk_trend_display_only_when_floor_unset(tmp_path):
    # Against a probe.py that still does `projected_days < None`, this raises.
    cfg = config(tmp_path, disk_days_to_full_min=None, disk_trend_sustained_samples=1)
    status = None
    # A steep, sustained decline that WOULD trip the trend at any sane floor.
    for index, free_blocks in enumerate((500, 480, 460, 440)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=1000, f_bfree=free_blocks)},
            now=1_000_000 + index * 60,
        )
    assert status["disk"]["days_to_full"] is not None  # diagnostics keep working
    assert status["disk"]["healthy"] is True
    assert status["disk"]["trend_display_only"] is True
    assert status["disk"]["trend_breach"] is False


def test_build_status_disk_trend_still_enforced_when_floor_is_set(tmp_path):
    # Regression guard: unchanged behaviour when a floor IS configured --
    # display-only must be opt-in via None, not the only path left standing.
    cfg = config(tmp_path, disk_days_to_full_min=3, disk_trend_sustained_samples=3)
    status = None
    for index, free_blocks in enumerate((2_883, 2_882, 2_881, 2_880)):
        status = probe.build_status(
            cfg,
            make_meminfo(8_000_000, 4_000_000),
            {"/": make_statvfs(f_blocks=10_000, f_bfree=free_blocks)},
            now=1_000_000 + index * 60,
        )
    assert status["disk"]["healthy"] is False
    assert status["disk"]["trend_breach"] is True
    assert status["disk"]["threshold_breach"] is False
    assert "trend_display_only" not in status["disk"]


def test_build_status_absolute_threshold_still_wins_with_trend_disabled(tmp_path):
    # The dangerous regression to guard against: disabling the trend must
    # never blunt the absolute threshold on the same host.
    cfg = config(tmp_path, disk_days_to_full_min=None)
    status = probe.build_status(
        cfg,
        make_meminfo(8_000_000, 4_000_000),
        {"/": make_statvfs(f_blocks=1000, f_bfree=20)},  # 98% used > 90% threshold
    )
    assert status["disk"]["healthy"] is False
    assert status["disk"]["threshold_breach"] is True
    assert status["disk"]["trend_display_only"] is True


def test_run_exits_nonzero_and_skips_status_write_on_missing_config(tmp_path):
    # run() reads /proc/meminfo + os.statvfs, which only exist on Linux (the
    # 6 target hosts) — this case fails before either is reached (bad config
    # path), so it stays portable and needs no injected OS fixtures.
    missing_config = tmp_path / "does-not-exist.json"
    rc = probe.run(config_path=str(missing_config))
    assert rc == 1
    assert not (tmp_path / "status.json").exists()
