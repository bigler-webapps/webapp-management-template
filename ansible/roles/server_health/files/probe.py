#!/usr/bin/env python3
"""server-health probe.

Reads disk / memory / swap usage, applies per-metric thresholds (memory and
swap require N consecutive sampled breaches before being reported down;
disk fires on a single sample), and writes the result atomically to
<state_dir>/status.json for the responder to serve.

Config-driven (no host-specific literals in this file): reads
/etc/server-health/config.json, templated per-host by the server_health
Ansible role from ansible/roles/server_health/defaults/main.yml + host_vars
overrides (see ansible/roles/server_health/templates/config.json.j2). This
file is deployed unmodified via `copy` (not `template`) so it can be
imported and unit-tested directly from the repo path — see
ansible/roles/server_health/tests/.

Failure mode: on any parse/read error this script
logs to stderr and exits non-zero WITHOUT touching status.json — it must
never write a result that could be read as healthy. The responder's own
staleness check (status.json missing or older than 2x the probe interval)
is what turns "no fresh data" into "down".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_CONFIG_PATH = "/etc/server-health/config.json"


class ProbeError(Exception):
    """Raised on any input that cannot be parsed — must never yield a
    'healthy' result."""


def parse_meminfo(text: str) -> dict:
    """Parse /proc/meminfo content into {key: value_kb}. Raises ProbeError
    on malformed input rather than silently defaulting."""
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            key, rest = line.split(":", 1)
            value = int(rest.strip().split()[0])
        except (ValueError, IndexError) as exc:
            raise ProbeError(f"unparseable /proc/meminfo line: {line!r}") from exc
        result[key] = value
    required = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    missing = [k for k in required if k not in result]
    if missing:
        raise ProbeError(f"/proc/meminfo missing keys: {missing}")
    return result


def mem_available_pct(meminfo: dict) -> float:
    total = meminfo["MemTotal"]
    if total <= 0:
        raise ProbeError("MemTotal is zero or negative")
    return 100.0 * meminfo["MemAvailable"] / total


def swap_used_pct(meminfo: dict) -> float:
    total = meminfo["SwapTotal"]
    if total <= 0:
        return 0.0  # no swap configured on this host — never a breach
    used = total - meminfo["SwapFree"]
    return 100.0 * used / total


def disk_used_pct(f_blocks: int, f_bfree: int) -> float:
    """f_blocks / f_bfree as returned by os.statvfs() on a mount point."""
    if f_blocks <= 0:
        raise ProbeError("statvfs f_blocks is zero or negative")
    used = f_blocks - f_bfree
    return 100.0 * used / f_blocks


class SustainedBreachTracker:
    """Tracks consecutive-breach counts per metric across probe runs,
    persisted to a small JSON state file so the timer's independent
    invocations share history."""

    def __init__(self, counts: dict | None = None) -> None:
        self.counts = dict(counts or {})

    @classmethod
    def load(cls, path: Path) -> "SustainedBreachTracker":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
            return cls({k: int(v) for k, v in data.items()})
        except (ValueError, OSError):
            # Corrupt state file — start clean rather than fail the probe;
            # this only delays a sustained-breach alert by up to N samples,
            # never causes a false "healthy".
            return cls()

    def save(self, path: Path) -> None:
        atomic_write(path, json.dumps(self.counts))

    def update(self, metric: str, breached: bool, required_samples: int) -> bool:
        """Returns True iff `metric` has now breached `required_samples`
        consecutive times. required_samples <= 1 means "fires on one
        sample" (used for disk)."""
        if breached:
            self.counts[metric] = self.counts.get(metric, 0) + 1
        else:
            self.counts[metric] = 0
        return self.counts[metric] >= max(1, required_samples)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def compute_disk(config: dict, disk_stats: dict) -> tuple:
    """disk_stats: {mount: os.statvfs() result}. Multiple mounts are
    aggregated: any mount over threshold is a breach; the reported value
    is the worst (highest) percentage."""
    threshold = config["disk_threshold_pct"]
    detail = {}
    worst_pct = 0.0
    any_breach = False
    for mount, stat in disk_stats.items():
        pct = disk_used_pct(stat.f_blocks, stat.f_bfree)
        detail[mount] = round(pct, 2)
        worst_pct = max(worst_pct, pct)
        if pct > threshold:
            any_breach = True
    return worst_pct, any_breach, detail


def build_status(config: dict, meminfo_text: str, disk_stats: dict) -> dict:
    """Pure core: given config + raw inputs, compute the status payload.
    Raises ProbeError on bad input — caller must not write status.json in
    that case. disk_stats: {mount: os.statvfs() result}."""
    meminfo = parse_meminfo(meminfo_text)
    mem_pct = mem_available_pct(meminfo)
    swap_pct = swap_used_pct(meminfo)
    disk_pct, disk_breach_raw, disk_detail = compute_disk(config, disk_stats)

    tracker = SustainedBreachTracker.load(Path(config["state_dir"]) / "breach_counts.json")

    disk_breach = tracker.update("disk", disk_breach_raw, required_samples=1)
    mem_breach = tracker.update(
        "mem",
        mem_pct < config["mem_available_min_pct"],
        required_samples=config["mem_sustained_samples"],
    )
    swap_display_only = config["swap_max_pct"] is None
    if swap_display_only:
        # Display-only: never a breach, regardless of value_pct. Reset rather
        # than accumulate so a later re-enabled threshold doesn't inherit a stale streak.
        tracker.counts["swap"] = 0
        swap_breach = False
    else:
        swap_breach = tracker.update(
            "swap",
            swap_pct > config["swap_max_pct"],
            required_samples=config["swap_sustained_samples"],
        )
    tracker.save(Path(config["state_dir"]) / "breach_counts.json")

    swap_entry = {"value_pct": round(swap_pct, 2), "healthy": not swap_breach}
    if swap_display_only:
        swap_entry["display_only"] = True

    return {
        "generated_at": time.time(),
        "disk": {"value_pct": round(disk_pct, 2), "healthy": not disk_breach, "mounts": disk_detail},
        "mem": {"value_pct": round(mem_pct, 2), "healthy": not mem_breach},
        "swap": swap_entry,
    }


def run(config_path: str = DEFAULT_CONFIG_PATH) -> int:
    try:
        config = load_config(config_path)
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            meminfo_text = fh.read()
        disk_stats = {m: os.statvfs(m) for m in config.get("disk_mounts", ["/"])}
        status = build_status(config, meminfo_text, disk_stats)
    except Exception as exc:  # noqa: BLE001 — any failure must exit non-zero, never write status
        print(f"server-health probe failed: {exc}", file=sys.stderr)
        return 1

    status_path = Path(config["state_dir"]) / "status.json"
    atomic_write(status_path, json.dumps(status))
    return 0


if __name__ == "__main__":
    sys.exit(run())
