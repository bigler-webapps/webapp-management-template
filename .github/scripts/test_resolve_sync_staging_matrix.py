#!/usr/bin/env python3
"""Tests for resolve_sync_staging_matrix.py.

Run with: python -m unittest test_resolve_sync_staging_matrix.py
"""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parent / "resolve_sync_staging_matrix.py"


def write_inventory(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    tmp.write(textwrap.dedent(content))
    tmp.close()
    return Path(tmp.name)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


class TestHappyPath(unittest.TestCase):
    def setUp(self):
        self.inventory = write_inventory(
            """
            version: 1
            targets:
              primary-prod:
                sync_staging_apps: [my-app, example-app, another-app]
              secondary-prod:
                sync_staging_apps: [other-app]
              staging:
                sync_staging_apps: []
            """
        )

    def tearDown(self):
        self.inventory.unlink(missing_ok=True)

    def test_emits_flat_matrix(self):
        result = run("--inventory", str(self.inventory))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        rows = payload["include"]
        self.assertEqual(len(rows), 4)
        sources_apps = [(r["source"], r["app"]) for r in rows]
        self.assertIn(("primary-prod", "my-app"), sources_apps)
        self.assertIn(("primary-prod", "example-app"), sources_apps)
        self.assertIn(("primary-prod", "another-app"), sources_apps)
        self.assertIn(("secondary-prod", "other-app"), sources_apps)

    def test_app_filter_restricts(self):
        result = run("--inventory", str(self.inventory), "--app-filter", "example-app")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        rows = payload["include"]
        self.assertEqual(rows, [{"source": "primary-prod", "app": "example-app"}])

    def test_app_filter_unmatched_fails(self):
        result = run("--inventory", str(self.inventory), "--app-filter", "ghost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No sync_staging_apps configured", result.stderr)


class TestEmptyInventory(unittest.TestCase):
    def test_all_empty_sync_staging_apps_fails(self):
        inv = write_inventory(
            """
            version: 1
            targets:
              a:
                sync_staging_apps: []
              b: {}
            """
        )
        try:
            result = run("--inventory", str(inv))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No sync_staging_apps configured", result.stderr)
        finally:
            inv.unlink(missing_ok=True)

    def test_missing_inventory_file_fails(self):
        result = run("--inventory", "/tmp/does-not-exist.yaml")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Inventory file not found", result.stderr)


class TestMalformedInput(unittest.TestCase):
    def test_non_list_sync_staging_apps_fails(self):
        inv = write_inventory(
            """
            version: 1
            targets:
              a:
                sync_staging_apps: "not-a-list"
            """
        )
        try:
            result = run("--inventory", str(inv))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid sync_staging_apps", result.stderr)
        finally:
            inv.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
