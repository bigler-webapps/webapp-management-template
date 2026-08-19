#!/usr/bin/env python3
"""server-health probe.

Reads disk / inode / memory / swap usage, applies per-metric thresholds (memory,
swap, and projected disk exhaustion require N consecutive sampled breaches;
the absolute disk threshold fires on a single sample), and writes the result atomically to
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


def inode_used_pct(f_files: int, f_ffree: int) -> float:
    """f_files / f_ffree as returned by os.statvfs() on a mount point."""
    if f_files <= 0:
        raise ProbeError("statvfs f_files is zero or negative")
    used = f_files - f_ffree
    return 100.0 * used / f_files


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


class DiskHistory:
    """Rolling free-space samples, persisted independently of breach counts."""

    def __init__(self, samples: dict | None = None) -> None:
        self.samples = dict(samples or {})

    @classmethod
    def load(cls, path: Path) -> "DiskHistory":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("history file is not a JSON object")
            samples = {}
            for mount, entries in data.items():
                if not isinstance(mount, str) or not isinstance(entries, list):
                    raise ValueError("history file has invalid entries")
                samples[mount] = [(float(ts), int(free)) for ts, free in entries]
            return cls(samples)
        except (TypeError, ValueError, OSError):
            # A corrupt history only loses the projection until the next two
            # samples; it must not make the load-bearing probe fail closed.
            return cls()

    def add(self, mount: str, timestamp: float, free_bytes: int, window_sec: int) -> list:
        entries = self.samples.setdefault(mount, [])
        entries.append((timestamp, free_bytes))
        self.prune(timestamp, window_sec)
        return self.samples[mount]

    def prune(self, timestamp: float, window_sec: int) -> None:
        """Keep all mounts' samples within the time-based retention window."""
        cutoff = timestamp - window_sec
        self.samples = {
            mount: [(ts, free) for ts, free in entries if ts >= cutoff]
            for mount, entries in self.samples.items()
        }
        self.samples = {mount: entries for mount, entries in self.samples.items() if entries}

    def save(self, path: Path) -> None:
        atomic_write(path, json.dumps(self.samples))


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


def stat_free_bytes(stat: object) -> int:
    """Return free bytes from statvfs, rejecting an invalid block size."""
    block_size = getattr(stat, "f_frsize", 0) or getattr(stat, "f_bsize", 0)
    if block_size <= 0:
        raise ProbeError("statvfs fragment size is zero or negative")
    return int(stat.f_bfree) * int(block_size)


def stat_total_bytes(stat: object) -> int:
    """Return total bytes from statvfs, rejecting an invalid block size.

    A fixed absolute-byte headroom floor cannot be a single fleet-wide
    constant -- a floor tuned against one host's disk size can sit ABOVE
    another, larger host's own absolute-threshold trip point, which would
    neuter the trend there entirely (it could never fire before the
    absolute threshold does). The floor is derived per-mount from this and
    disk_threshold_pct instead, so it scales with each host's own disk size
    automatically -- no per-host override to remember."""
    block_size = getattr(stat, "f_frsize", 0) or getattr(stat, "f_bsize", 0)
    if block_size <= 0:
        raise ProbeError("statvfs fragment size is zero or negative")
    return int(stat.f_blocks) * int(block_size)


def days_to_full(samples: list[tuple[float, int]]) -> float | None:
    """Project exhaustion from the oldest/newest samples in the time window.

    The configured window makes a deploy afternoon contribute only its
    share of a longer period, while a fill that persists over the full
    window remains visible. The sustained-breach counter absorbs residual
    two-point slope noise.
    """
    if len(samples) < 2:
        return None
    oldest_time, oldest_free = samples[0]
    newest_time, newest_free = samples[-1]
    elapsed = newest_time - oldest_time
    consumed = oldest_free - newest_free
    if elapsed <= 0 or consumed <= 0:
        return None
    return newest_free / (consumed / elapsed) / 86_400


GROWTH_WATCH_REPORT_DEPTH = 2
GROWTH_WATCH_RECURSE_DEPTH = 8
GROWTH_WATCH_LIMIT = 5
GROWTH_WATCH_TIME_BUDGET_SEC = 5.0


def top_growth_directories(watch_dirs: list[str], *, clock=time.monotonic) -> list[dict]:
    """Return the largest bounded-depth directory trees for incident detail.

    Two different bounds, deliberately not the same number:
    - GROWTH_WATCH_RECURSE_DEPTH bounds how deep the SIZE COMPUTATION
      recurses, so a total is still correct. Docker's real layer content
      lives at overlay2/<hash>/diff/... (depth 3 under /var/lib/docker) and
      containerd's snapshotter content nests even deeper
      (io.containerd.snapshotter.v1.overlayfs/snapshots/<id>/fs/... , depth
      4+ under /var/lib/containerd) -- stopping at depth 2, as an earlier
      version of this function did, silently reported near-zero for exactly
      the directories this feature exists to attribute, because the actual
      file content one level below was never summed. 8 comfortably covers
      both known layouts with margin, while still bounding worst-case
      runtime against an unrelated, arbitrarily-deep watch root.
    - GROWTH_WATCH_REPORT_DEPTH bounds which depth of directory gets its own
      entry in the returned list -- readability, not correctness. A layer
      hash directory's full (correctly recursed) size is attributed to that
      directory itself; reporting doesn't need one row per file.

    Missing watch roots are normal on hosts without Docker/containerd.
    Unreadable ones are normal too: the probe runs as an unprivileged
    `server-health` nologin user (see tasks/main.yml) with no docker-group
    membership or ACL grant, and Docker's data root is conventionally
    root-only (0710/0711) -- individual layer content can carry arbitrary
    container-file ownership on top of that. A PermissionError here is
    therefore an expected environmental condition, not a bug: it must
    degrade this diagnostic (skip the unreadable subtree, contribute 0),
    never take down disk/mem/swap reporting by propagating to run()'s
    fail-loud boundary. Review finding: an earlier version let this
    propagate, which would have broken the whole probe's status.json the
    moment root-owned content was hit on any host.

    Runtime is bounded two ways, per the WO's own risk ("bound the depth AND
    the runtime"): GROWTH_WATCH_RECURSE_DEPTH caps how deep any single branch
    goes, and GROWTH_WATCH_TIME_BUDGET_SEC caps total wall-clock time across
    the whole scan -- a host with far more images/layers than expected stops
    descending once the budget is spent rather than delaying the next
    status.json write indefinitely. Deadline is checked once per directory,
    not per file, so a large single directory can't blow past it.
    """
    candidates: list[dict] = []
    deadline = clock() + GROWTH_WATCH_TIME_BUDGET_SEC

    def scan(path: Path, depth: int) -> int:
        if clock() >= deadline:
            return 0
        total = 0
        try:
            entries_cm = os.scandir(path)
        except (PermissionError, OSError):
            return 0
        with entries_cm as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child = Path(entry.path)
                        child_size = scan(child, depth + 1) if depth < GROWTH_WATCH_RECURSE_DEPTH else 0
                        if depth < GROWTH_WATCH_REPORT_DEPTH:
                            candidates.append({"path": str(child), "size_bytes": child_size})
                        total += child_size
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except (PermissionError, OSError):
                    continue
        return total

    for root_name in watch_dirs:
        root = Path(root_name)
        if not root.is_dir():
            continue
        scan(root, 0)
    return sorted(candidates, key=lambda item: (-item["size_bytes"], item["path"]))[:GROWTH_WATCH_LIMIT]


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


def compute_inodes(config: dict, disk_stats: dict) -> tuple:
    """Aggregate inode usage in the same way as disk block usage."""
    threshold = config["inode_threshold_pct"]
    detail = {}
    worst_pct = 0.0
    any_breach = False
    for mount, stat in disk_stats.items():
        pct = inode_used_pct(stat.f_files, stat.f_ffree)
        detail[mount] = round(pct, 2)
        worst_pct = max(worst_pct, pct)
        if pct > threshold:
            any_breach = True
    return worst_pct, any_breach, detail


def build_status(config: dict, meminfo_text: str, disk_stats: dict, now: float | None = None) -> dict:
    """Pure core: given config + raw inputs, compute the status payload.
    Raises ProbeError on bad input — caller must not write status.json in
    that case. disk_stats: {mount: os.statvfs() result}."""
    meminfo = parse_meminfo(meminfo_text)
    mem_pct = mem_available_pct(meminfo)
    swap_pct = swap_used_pct(meminfo)
    now = time.time() if now is None else now
    disk_pct, disk_breach_raw, disk_detail = compute_disk(config, disk_stats)
    inode_pct, inode_breach, inode_detail = compute_inodes(config, disk_stats)

    history_path = Path(config["state_dir"]) / "disk_history.json"
    history = DiskHistory.load(history_path)
    trend_by_mount = {}
    free_bytes_by_mount = {}
    total_bytes_by_mount = {}
    for mount, stat in disk_stats.items():
        free_bytes = stat_free_bytes(stat)
        free_bytes_by_mount[mount] = free_bytes
        total_bytes_by_mount[mount] = stat_total_bytes(stat)
        samples = history.add(
            mount,
            now,
            free_bytes,
            config["disk_trend_window_sec"],
        )
        trend_by_mount[mount] = days_to_full(samples)
    history.save(history_path)
    finite_trends = [days for days in trend_by_mount.values() if days is not None]
    projected_days = min(finite_trends) if finite_trends else None
    growth = top_growth_directories(config["growth_watch_dirs"])

    tracker = SustainedBreachTracker.load(Path(config["state_dir"]) / "breach_counts.json")

    disk_breach = tracker.update("disk", disk_breach_raw, required_samples=1)
    days_min = config["disk_days_to_full_min"]
    margin_pct = config.get("disk_trend_floor_margin_pct")

    def mount_would_breach(mount: str, days: float | None) -> bool:
        if days_min is None or days is None or days >= days_min:
            return False
        if margin_pct is not None:
            # Derived per-mount from this host's OWN total size and its OWN
            # absolute threshold, not a fixed global byte count. The trend is
            # vetoed while free space is still comfortably above where the
            # absolute threshold would fire -- margin_pct is how much extra
            # headroom, in percentage points, is required beyond that.
            total = total_bytes_by_mount[mount]
            if total > 0:
                # disk_threshold_pct is a USED percentage, so the free-space
                # equivalent of "margin_pct points inside it" is
                # 100 - (threshold - margin), not (threshold - margin) itself.
                veto_free_pct = 100.0 - (config["disk_threshold_pct"] - margin_pct)
                veto_free_bytes = total * veto_free_pct / 100.0
                if free_bytes_by_mount[mount] >= veto_free_bytes:
                    return False  # Ample absolute headroom vetoes the projection.
        return True

    trend_breach_candidate = any(
        mount_would_breach(mount, days) for mount, days in trend_by_mount.items()
    )
    slope_alone_would_breach = any(
        days_min is not None and days is not None and days < days_min
        for days in trend_by_mount.values()
    )
    # One display-only contract: a host may opt out statically (days_min is
    # null), or a sample may be dynamically vetoed while all projected mounts
    # have ample headroom. The projection remains published in both cases.
    disk_trend_display_only = days_min is None or (
        slope_alone_would_breach and not trend_breach_candidate
    )
    disk_trend_breach = tracker.update(
        "disk_trend",
        trend_breach_candidate,
        required_samples=config["disk_trend_sustained_samples"],
    )
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

    disk_entry = {
        "value_pct": round(disk_pct, 2),
        "healthy": not (disk_breach or disk_trend_breach),
        "mounts": disk_detail,
        "days_to_full": None if projected_days is None else round(projected_days, 2),
        "trend_mounts": {
            mount: None if days is None else round(days, 2) for mount, days in trend_by_mount.items()
        },
        "growth": growth,
        # The two verdicts that used to collapse into a single boolean
        # ("healthy") are kept apart so the responder can name which one
        # fired instead of always reporting "threshold breached".
        "threshold_breach": disk_breach,
        "trend_breach": disk_trend_breach,
    }
    if disk_trend_display_only:
        disk_entry["trend_display_only"] = True

    return {
        "generated_at": now,
        "disk": disk_entry,
        "inode": {"value_pct": round(inode_pct, 2), "healthy": not inode_breach, "mounts": inode_detail},
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
