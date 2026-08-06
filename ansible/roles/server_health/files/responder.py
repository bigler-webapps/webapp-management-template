#!/usr/bin/env python3
"""server-health responder.

Persistent HTTP service; serves the probe's latest status.json as
per-metric health-check endpoints for an ordinary HTTP monitor (e.g.
Uptime Kuma) — no push monitor, no token/secret. Bound to the host's
tailnet interface only (see
ansible/roles/server_health/defaults/main.yml — server_health_listen_addr);
never exposed publicly, no reverse proxy/domain/terraform involved.

GET /health/<metric>   metric in {disk, mem, swap}
GET /health            all three, JSON summary

Response: 200 if healthy and fresh, 503 if breached OR the status file is
missing/stale (stale = older than 2 * probe_interval_sec + 30s —
"no data" must never look healthy). Body is always JSON:
{"metric": ..., "value_pct": float|null, "healthy": bool, "reason": str}

Deployed unmodified via `copy` (not `template`) — no host-specific
literals in this file, all configuration comes in via CLI args from the
systemd unit (see templates/server-health-responder.service.j2) — so it
can be imported and unit-tested directly. See
ansible/roles/server_health/tests/.
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

METRICS = ("disk", "mem", "swap")


def load_status(status_path: Path) -> dict | None:
    try:
        return json.loads(status_path.read_text())
    except (OSError, ValueError):
        return None


def evaluate_metric(
    status: dict | None,
    metric: str,
    status_mtime,
    now: float,
    max_age_sec: float,
) -> tuple:
    """Pure decision function — the staleness/breach logic, kept separate
    from file I/O and socket handling so it is directly unit testable."""
    if status is None or status_mtime is None:
        return 503, {
            "metric": metric,
            "value_pct": None,
            "healthy": False,
            "reason": "no status data",
        }
    age = now - status_mtime
    if age > max_age_sec:
        return 503, {
            "metric": metric,
            "value_pct": None,
            "healthy": False,
            "reason": f"stale status ({age:.0f}s old, max {max_age_sec:.0f}s)",
        }
    entry = status.get(metric)
    if entry is None:
        return 503, {
            "metric": metric,
            "value_pct": None,
            "healthy": False,
            "reason": f"unknown metric {metric!r}",
        }
    healthy = bool(entry.get("healthy"))
    code = 200 if healthy else 503
    if healthy and entry.get("display_only"):
        # Distinguishable from a metric that simply passed its
        # threshold -- a future reader must not mistake a disabled check for a healthy one.
        reason = "display-only (no threshold configured)"
    else:
        reason = "ok" if healthy else "threshold breached"
    return code, {
        "metric": metric,
        "value_pct": entry.get("value_pct"),
        "healthy": healthy,
        "reason": reason,
    }


def make_handler(status_path: Path, probe_interval_sec: float):
    max_age_sec = 2 * probe_interval_sec + 30

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — stdlib method name
            path = self.path.rstrip("/")
            now = time.time()
            try:
                mtime = status_path.stat().st_mtime
            except OSError:
                mtime = None
            status = load_status(status_path) if mtime is not None else None

            if path == "/health":
                results = {}
                worst_code = 200
                for m in METRICS:
                    code, payload = evaluate_metric(status, m, mtime, now, max_age_sec)
                    results[m] = payload
                    worst_code = max(worst_code, code)
                self._respond(worst_code, results)
                return

            for m in METRICS:
                if path == f"/health/{m}":
                    code, payload = evaluate_metric(status, m, mtime, now, max_age_sec)
                    self._respond(code, payload)
                    return

            self._respond(404, {"error": "not found"})

        def log_message(self, fmt: str, *args) -> None:  # quieter default logging
            sys.stderr.write("server-health-responder: " + (fmt % args) + "\n")

    return Handler


def run(status_path: str, listen_addr: str, listen_port: int, probe_interval_sec: float) -> None:
    handler = make_handler(Path(status_path), probe_interval_sec)
    server = ThreadingHTTPServer((listen_addr, listen_port), handler)
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--status-path", default="/var/lib/server-health/status.json")
    parser.add_argument("--listen-addr", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9110)
    parser.add_argument("--probe-interval-sec", type=float, default=60)
    args = parser.parse_args()
    run(args.status_path, args.listen_addr, args.listen_port, args.probe_interval_sec)
