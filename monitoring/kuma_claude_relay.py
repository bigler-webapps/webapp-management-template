#!/usr/bin/env python3
"""
Kuma -> Claude Relay
====================
Runs as a Docker service on every app server.
Receives Kuma webhooks (DOWN events), enriches them with Docker logs, and
forwards them to the Claude Code Routine API.

Reachable via Tailscale Serve:
  https://<your-server>.<your-tailnet>.ts.net:8090/webhook

Docker logs come from docker-socket-proxy-relay (CONTAINERS=1, LOGS=1).

Secrets (via .env -> sync-secrets -> secret store):
  CLAUDE_ROUTINE_ENDPOINT  -- API URL of the Kuma-Alert Routine
  CLAUDE_ROUTINE_TOKEN     -- Bearer token for the Routine (sk-ant-oat01-...)
"""

import json
import logging
import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import docker
import requests

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------

CLAUDE_ROUTINE_ENDPOINT = os.environ["CLAUDE_ROUTINE_ENDPOINT"]
CLAUDE_ROUTINE_TOKEN = os.environ["CLAUDE_ROUTINE_TOKEN"]

RELAY_PORT = 8090
LOG_LINES = 150

# Docker access via socket proxy (never a direct socket mount)
DOCKER_HOST = os.environ.get("DOCKER_HOST", "tcp://docker-socket-proxy-relay:2375")

# Monitor name (substring, lowercase) -> Docker Compose project + service
# Compose project = directory name under /srv/apps/<project>
# Order: longest keys first (so more specific names match before shorter ones)
#
# TODO: Adapt this mapping to your actual app names and Docker Compose projects.
MONITOR_MAP = {
    # "<monitor-name-substring>": {"compose_project": "<project-name>", "service": "<service>"},
    # Example:
    # "myapp":   {"compose_project": "myapp",   "service": "backend"},
    # "otherapp": {"compose_project": "otherapp", "service": "web"},
}

# Deduplication: fire Claude Routine at most once per compose project per window.
# Prevents duplicate sessions when multiple monitors (frontend + healthz) go DOWN
# simultaneously for the same app.
DEDUP_WINDOW = timedelta(minutes=5)
_recent_fires: dict[str, datetime] = {}


def _already_fired(compose_project: str) -> bool:
    """Return True and skip if this project already fired within DEDUP_WINDOW."""
    last = _recent_fires.get(compose_project)
    if last and (datetime.now() - last) < DEDUP_WINDOW:
        return True
    _recent_fires[compose_project] = datetime.now()
    return False


# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def match_monitor(monitor_name: str) -> dict | None:
    name_lower = monitor_name.lower()
    for key in sorted(MONITOR_MAP.keys(), key=len, reverse=True):
        if key in name_lower:
            return {**MONITOR_MAP[key], "key": key}
    return None


def fetch_docker_logs(compose_project: str, service: str) -> str:
    try:
        client = docker.DockerClient(base_url=DOCKER_HOST)
        containers = client.containers.list(
            filters={
                "label": [
                    f"com.docker.compose.project={compose_project}",
                    f"com.docker.compose.service={service}",
                ],
                "status": "running",
            }
        )
        if not containers:
            return f"(no running container for project={compose_project} service={service})"

        container = containers[0]
        raw = container.logs(tail=LOG_LINES, timestamps=False, stream=False)
        return raw.decode("utf-8", errors="replace").strip() or "(no log output)"

    except Exception as exc:
        return f"(error fetching logs: {exc})"


def fire_claude_routine(monitor_name: str, status_msg: str, config: dict, docker_logs: str) -> None:
    enriched = (
        f"KUMA ALERT -- Monitor DOWN\n"
        f"Monitor:         {monitor_name}\n"
        f"Message:         {status_msg}\n"
        f"Compose-Project: {config['compose_project']}\n"
        f"Service:         {config['service']}\n"
        f"\n"
        f"DOCKER LOGS (last {LOG_LINES} lines):\n"
        f"{'─' * 60}\n"
        f"{docker_logs}\n"
        f"{'─' * 60}\n"
    )

    resp = requests.post(
        CLAUDE_ROUTINE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {CLAUDE_ROUTINE_TOKEN}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"text": enriched},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    log.info(
        "Claude Routine started: session_id=%s  url=%s",
        data.get("session_id"),
        data.get("session_url"),
    )


class RelayHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Return 200 immediately -- Kuma does not wait long
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            log.warning("Invalid JSON body received")
            return

        monitor_name = payload.get("monitor", {}).get("name", "")
        status = payload.get("heartbeat", {}).get("status", 1)  # 0=down, 1=up
        status_msg = payload.get("heartbeat", {}).get("msg", payload.get("msg", ""))

        if status != 0:
            log.info("Monitor '%s' is UP -- no action needed", monitor_name)
            return

        config = match_monitor(monitor_name)
        if not config:
            log.warning(
                "No mapping for monitor '%s' -- extend MONITOR_MAP", monitor_name
            )
            return

        if _already_fired(config["compose_project"]):
            log.info(
                "DEDUP: '%s' -> Routine for '%s' already fired within %s -- skipping",
                monitor_name,
                config["compose_project"],
                DEDUP_WINDOW,
            )
            return

        log.info(
            "DOWN: '%s' -> fetching logs for %s/%s ...",
            monitor_name,
            config["compose_project"],
            config["service"],
        )
        docker_logs = fetch_docker_logs(config["compose_project"], config["service"])

        log.info("Forwarding to Claude Routine ...")
        try:
            fire_claude_routine(monitor_name, status_msg, config, docker_logs)
        except requests.HTTPError as exc:
            log.error(
                "Claude API error: %s -- %s", exc.response.status_code, exc.response.text
            )
        except Exception as exc:
            log.error("Unexpected error forwarding alert: %s", exc, exc_info=True)

    def log_message(self, fmt, *args):  # suppress stdlib HTTP logs
        pass


def main():
    server = HTTPServer(("0.0.0.0", RELAY_PORT), RelayHandler)
    log.info("Kuma-Claude-Relay running on port %d", RELAY_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
