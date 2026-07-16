#!/usr/bin/env python3
"""Derive the app/server registry from each repo's project.yaml.

Single source of truth: every app repo declares, in its own project.yaml,
`environments.production.server` (where it is deployed) and optionally
`staging_sync: false` (opt-out of the prod->staging data sync; default true).
Grouping these by server replaces the hand-maintained
`inventory.targets.<srv>.sync_staging_apps` list and the hardcoded `APPS="..."`
lists in kuma-sync.yml.

Two distinct per-server lists are produced (do NOT conflate them):
  - deployed_apps: ALL apps whose production.server == <srv>. Drives the
    maintenance health check's expected_container_tokens — an app keeps running
    on the server regardless of staging_sync, so it MUST stay here.
  - sync_apps:     deployed_apps minus those with `staging_sync: false`. Drives
    the prod->staging cross-server-restore (sync-staging) matrix only.

Two input modes share one parsing core:
  --repos-dir DIR  Read DIR/*/project.yaml from a local checkout. Used for unit
                   tests and the local equivalence gate (no token needed).
  --github-app     Enumerate org repos via GET /installation/repositories using
                   the GitHub App installation token in env GH_APP_TOKEN, then
                   read each repo's project.yaml. Used by the CI workflows.

Fail-closed (data safety — a silently shrunk matrix means skipped backups):
  - a repo whose project.yaml EXISTS but is malformed -> abort (never skip)
  - any GitHub API/transport error -> abort
  - --min-apps N: abort if fewer than N apps resolved in total
A repo WITHOUT project.yaml (404) is legitimately "not an app" -> skipped.

Output (stdout): JSON
  {"servers": {"<srv>": {"deployed_apps": [...], "sync_apps": [...],
                          "domains": [...]}, ...}}
project_name (not the repo name) is used as the app token, matching the legacy
inventory values (e.g. a legacy app's repo name can differ from its project_name (e.g. repo "legacy-app", project_name "app")).
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

import yaml

GITHUB_API = "https://api.github.com"
ORG = "YOUR_GITHUB_ORG"  # <- set to your GitHub org/user


def fail(message: str) -> NoReturn:
    print(f"❌ {message}", file=sys.stderr)
    sys.exit(1)


def parse_project_yaml(text: str, origin: str) -> dict | None:
    """Parse one project.yaml. Return a normalized app dict, or None if the repo
    is not a deployable app (no valid project_name / no production server).

    Raises (-> caller aborts) only on malformed YAML, never on "not an app".
    """
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        fail(f"Malformed project.yaml in {origin}: {exc}")

    if not isinstance(data, dict):
        fail(f"project.yaml in {origin} is not a mapping")

    project_name = str(data.get("project_name") or "").strip()
    # Template placeholders like "<your-app-slug>" are not real apps.
    if not project_name or "<" in project_name:
        return None

    # Explicit full opt-out: the app may declare a production.server (deploy
    # intent) but is NOT actually deployed on our infrastructure (e.g. WIP, or
    # hosted elsewhere). `registry: false` removes it from the ops registry
    # entirely — no backup/health/sync/monitoring. Distinct from staging_sync
    # (which keeps the app in health/monitoring, only skipping the data sync).
    in_registry = data.get("registry", True)
    if not isinstance(in_registry, bool):
        fail(f"registry flag in {origin} must be a boolean, got {in_registry!r}")
    if not in_registry:
        return None

    envs = data.get("environments") or {}
    if not isinstance(envs, dict):
        fail(f"environments in {origin} is not a mapping")
    prod = envs.get("production") or {}
    server = str((prod.get("server") if isinstance(prod, dict) else "") or "").strip()
    if not server:
        # Has a project_name but no production server -> infra/library/local-only
        # repo (e.g. infrastructure, photogallery without prod). Not in registry.
        return None

    # staging_sync defaults to True; only an explicit false opts out of the sync.
    staging_sync = data.get("staging_sync", True)
    if not isinstance(staging_sync, bool):
        fail(f"staging_sync in {origin} must be a boolean, got {staging_sync!r}")

    domains = prod.get("domains") or [] if isinstance(prod, dict) else []
    if not isinstance(domains, list):
        fail(f"production.domains in {origin} is not a list")

    return {
        "repo": origin,
        "project_name": project_name,
        "server": server,
        "staging_sync": staging_sync,
        "use_traefik": bool(prod.get("use_traefik", True)) if isinstance(prod, dict) else True,
        "domains": [str(d).strip() for d in domains if str(d).strip()],
    }


def load_exclude(infra_config: Path) -> set[str]:
    """Infra-side opt-out for repos we can't self-flag (e.g. externally-hosted
    repos that declare one of our servers but don't run on our infra). Read from
    the infra repo's own project.yaml `infra.registry_exclude`. Matched
    case-insensitively against both the repo name and the project_name. Absent
    file / key -> no exclusions. (Apps we control should prefer self-declaring
    `registry: false` in their own project.yaml; this list is for the rest.)"""
    if not infra_config.exists():
        return set()
    try:
        data = yaml.safe_load(infra_config.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        fail(f"Failed to parse infra config {infra_config}: {exc}")
    excludes = ((data.get("infra") or {}).get("registry_exclude")) or []
    if not isinstance(excludes, list):
        fail(f"infra.registry_exclude in {infra_config} must be a list")
    return {str(name).strip().lower() for name in excludes if str(name).strip()}


def build_servers(apps: list[dict]) -> dict:
    """Group normalized app dicts by server into deployed/sync/domains lists."""
    servers: dict[str, dict] = {}
    for app in apps:
        srv = servers.setdefault(
            app["server"], {"deployed_apps": [], "sync_apps": [], "domains": []}
        )
        srv["deployed_apps"].append(app["project_name"])
        if app["staging_sync"]:
            srv["sync_apps"].append(app["project_name"])
        srv["domains"].extend(app["domains"])
    # Deterministic, de-duplicated output.
    for srv in servers.values():
        srv["deployed_apps"] = sorted(set(srv["deployed_apps"]))
        srv["sync_apps"] = sorted(set(srv["sync_apps"]))
        srv["domains"] = sorted(set(srv["domains"]))
    return servers


def derive_from_dir(repos_dir: Path) -> list[dict]:
    if not repos_dir.is_dir():
        fail(f"--repos-dir not a directory: {repos_dir}")
    apps: list[dict] = []
    for project_file in sorted(repos_dir.glob("*/project.yaml")):
        text = project_file.read_text(encoding="utf-8")
        app = parse_project_yaml(text, origin=str(project_file.parent.name))
        if app is not None:
            apps.append(app)
    return apps


_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4  # 1 try + 3 retries; backoff 1s, 2s, 4s


def _gh_get(url: str, token: str) -> tuple[object, dict]:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    # Transient GitHub 5xx (notably intermittent 502 on the contents API) used to
    # abort the whole registry-prepare run; retry a few times with backoff before
    # failing closed. A 404 is authoritative (repo has no project.yaml) — never retried.
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(url) from exc
            if exc.code in _RETRY_STATUS and not last:
                time.sleep(2 ** attempt)
                continue
            fail(f"GitHub API error {exc.code} for {url}: {exc.read().decode('utf-8', 'replace')[:200]}")
        except urllib.error.URLError as exc:
            if not last:
                time.sleep(2 ** attempt)
                continue
            fail(f"GitHub API transport error for {url}: {exc}")


def _next_link(headers: dict) -> str | None:
    link = headers.get("Link") or headers.get("link") or ""
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) >= 2 and 'rel="next"' in seg[1]:
            return seg[0].strip().strip("<>")
    return None


def derive_from_github(token: str) -> list[dict]:
    # Enumerate every repo the App installation can see. App is installed
    # org-wide ("All repositories"), so this is the full org. The fail-closed
    # guards + --min-apps floor catch a silently narrowed installation.
    repos: list[str] = []
    url = f"{GITHUB_API}/installation/repositories?per_page=100"
    while url:
        payload, headers = _gh_get(url, token)
        for repo in payload.get("repositories", []):
            repos.append(repo["name"])
        url = _next_link(headers)
    if not repos:
        fail("GET /installation/repositories returned no repos (token/installation scope?)")

    apps: list[dict] = []
    for name in sorted(repos):
        contents_url = f"{GITHUB_API}/repos/{ORG}/{name}/contents/project.yaml"
        try:
            payload, _ = _gh_get(contents_url, token)
        except FileNotFoundError:
            # No project.yaml -> not an app repo. Legitimate skip.
            continue
        if not isinstance(payload, dict) or "content" not in payload:
            fail(f"Unexpected contents API response for {name}/project.yaml (not a file?)")
        text = base64.b64decode(payload["content"]).decode("utf-8")
        app = parse_project_yaml(text, origin=name)
        if app is not None:
            apps.append(app)
    return apps


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive app/server registry from project.yaml files.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--repos-dir", help="Local directory containing <repo>/project.yaml checkouts.")
    src.add_argument("--github-app", action="store_true", help="Enumerate org repos via the GitHub App token in GH_APP_TOKEN.")
    parser.add_argument("--min-apps", type=int, default=0, help="Fail-closed floor: abort if fewer than N apps resolve.")
    parser.add_argument("--list-repos", action="store_true", help="Print qualifying app repo names (one per line) instead of the registry JSON. Used by kuma-sync to clone each project.yaml.")
    parser.add_argument("--infra-config", default="project.yaml", help="the infra repo's project.yaml providing infra.registry_exclude (repos declaring a server but not deployed on our infra, e.g. externally-hosted). Read when present.")
    args = parser.parse_args()

    if args.github_app:
        token = os.environ.get("GH_APP_TOKEN", "").strip()
        if not token:
            fail("--github-app requires GH_APP_TOKEN in the environment")
        apps = derive_from_github(token)
    else:
        apps = derive_from_dir(Path(args.repos_dir))

    exclude = load_exclude(Path(args.infra_config))
    if exclude:
        apps = [a for a in apps if a["repo"].lower() not in exclude and a["project_name"].lower() not in exclude]

    if args.min_apps and len(apps) < args.min_apps:
        fail(f"Resolved only {len(apps)} apps, below --min-apps={args.min_apps} (degraded run?).")

    if args.list_repos:
        for repo in sorted({app["repo"] for app in apps}):
            print(repo)
        return

    # `servers` = per-server grouping (consumed by resolve_*_matrix / inventory_targets).
    # `apps` = flat per-app list (repo, project_name, server, domains, flags) for
    # external consumers like cockpit, which need the repo<->project_name mapping.
    out = {
        "servers": build_servers(apps),
        "apps": sorted(apps, key=lambda a: a["repo"].lower()),
    }
    print(json.dumps(out, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
