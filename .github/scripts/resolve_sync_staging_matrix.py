#!/usr/bin/env python3
"""Build a flat (source-target, app) matrix from inventory.sync_staging_apps.

Used by the sync-staging workflow to dispatch one cross-server-restore call per
app, sourced from the inventory target where the app currently lives.

Output (stdout): JSON object {"include": [{"source": "...", "app": "..."}]}
suitable for GH Actions `strategy.matrix: ${{ fromJson(...) }}` consumption.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml


def fail(message: str) -> None:
    print(f"❌ {message}", file=sys.stderr)
    sys.exit(1)


def load_inventory(path: Path) -> dict:
    if not path.exists():
        fail(f"Inventory file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        fail(f"Failed to parse inventory file: {exc}")
    targets = data.get("targets")
    if not isinstance(targets, dict) or not targets:
        fail("Inventory must contain a non-empty 'targets' mapping")
    return targets


def build_matrix(targets: dict, app_filter: str | None, only_sources: set[str] | None) -> list[dict]:
    """Expand inventory.sync_staging_apps into a flat list of {source, app} rows.

    `only_sources`: if set, drop rows whose source is not in the set
    (used to filter the matrix to targets whose backup succeeded).
    """
    rows: list[dict] = []
    for target_name, raw_target in targets.items():
        if only_sources is not None and target_name not in only_sources:
            continue
        target = raw_target or {}
        apps = target.get("sync_staging_apps") or []
        if not isinstance(apps, list):
            fail(f"Target '{target_name}' has invalid sync_staging_apps (expected list)")
        for app in apps:
            if not isinstance(app, str) or not app:
                fail(f"Target '{target_name}' has invalid app entry: {app!r}")
            if app_filter and app != app_filter:
                continue
            rows.append({"source": target_name, "app": app})
    return rows


def load_succeeded_targets(path: Path) -> set[str]:
    """Read succeeded-targets file written by the workflow's gh-api step."""
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve the sync-staging cross-server matrix from inventory."
    )
    parser.add_argument(
        "--inventory",
        default="inventory/inventory.yaml",
        help="Path to the inventory YAML file.",
    )
    parser.add_argument(
        "--app-filter",
        default="",
        help="Optional: restrict matrix to a single app name (skips other entries).",
    )
    parser.add_argument(
        "--filter-succeeded-file",
        default="",
        help=(
            "Optional: restrict matrix to source-targets listed (one per line) in this file. "
            "Used by sync-staging to keep only targets whose backup succeeded. "
            "Missing file → no filtering applied."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="If set, do not fail when the resolved matrix has zero rows (emit empty include).",
    )
    args = parser.parse_args()

    targets = load_inventory(Path(args.inventory))
    only_sources: set[str] | None = None
    if args.filter_succeeded_file:
        only_sources = load_succeeded_targets(Path(args.filter_succeeded_file))
    rows = build_matrix(targets, args.app_filter.strip() or None, only_sources)

    if not rows and not args.allow_empty:
        fail("No sync_staging_apps configured in any inventory target.")

    print(json.dumps({"include": rows}, separators=(",", ":")))


if __name__ == "__main__":
    main()
