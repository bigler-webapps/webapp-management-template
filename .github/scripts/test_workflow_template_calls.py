#!/usr/bin/env python3
"""Structural tests for the workflows' use of the workflow-templates composites.

Every `uses: <org>/workflow-templates/.github/actions/<name>@<ref>` must

* sit on the ONE version recorded in workflow_templates_inputs.json,
* name a composite that exists at that version,
* pass only inputs the composite declares and every input it requires
  (without a default),

and no workflow may pass an SSH private key (`ssh_private_key`,
`ssh_private_key_root`) or call a composite that was removed. The JSON is the
`inputs:` of every composite's action.yml at the recorded tag; regenerate it
when the pin moves.

Run with: python -m unittest test_workflow_template_calls.py
"""

import json
import re
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).parent
WORKFLOWS = sorted((HERE.parent / "workflows").glob("*.yml"))
SPEC = json.loads((HERE / "workflow_templates_inputs.json").read_text(encoding="utf-8"))
USES = re.compile(
    r"^(?:[\w.-]+)/workflow-templates/\.github/actions/(?P<name>[\w-]+)@(?P<ref>\S+)$"
)
REMOVED = {"provision-server", "sync-ssh-access", "update-server"}
SSH_KEY_INPUTS = {"ssh_private_key", "ssh_private_key_root"}


def violations(step, match, spec=SPEC):
    """Everything wrong with one composite call, as a list of messages."""
    found = []
    comp = spec["composites"].get(match["name"])
    passed = set((step.get("with") or {}).keys())
    if match["ref"] != spec["version"]:
        found.append(f"ref {match['ref']} != {spec['version']}")
    if match["name"] in REMOVED or comp is None:
        found.append(f"composite {match['name']} does not exist at {spec['version']}")
        return found
    found += [f"undeclared input {k}" for k in sorted(passed - set(comp["inputs"]))]
    found += [f"missing required input {k}" for k in sorted(set(comp["required"]) - passed)]
    found += [f"ssh key input {k}" for k in sorted(passed & SSH_KEY_INPUTS)]
    return found


def composite_calls():
    """Yield (workflow file name, job, step with a workflow-templates composite)."""
    for path in WORKFLOWS:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                match = USES.match(step.get("uses", ""))
                if match:
                    yield path.name, job_name, step, match


class WorkflowTemplateCallTests(unittest.TestCase):
    def test_calls_exist(self):
        self.assertTrue(list(composite_calls()), "no workflow-templates composite call found")

    def test_all_calls_on_the_recorded_version(self):
        for name, job, _step, match in composite_calls():
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertEqual(match["ref"], SPEC["version"])

    def test_every_composite_exists_at_that_version(self):
        for name, job, _step, match in composite_calls():
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertIn(match["name"], SPEC["composites"])
                self.assertNotIn(match["name"], REMOVED)

    def test_only_declared_inputs_are_passed(self):
        for name, job, step, match in composite_calls():
            declared = set(SPEC["composites"].get(match["name"], {}).get("inputs", []))
            passed = set((step.get("with") or {}).keys())
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertEqual(sorted(passed - declared), [])

    def test_required_inputs_are_passed(self):
        for name, job, step, match in composite_calls():
            required = set(SPEC["composites"].get(match["name"], {}).get("required", []))
            passed = set((step.get("with") or {}).keys())
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertEqual(sorted(required - passed), [])

    def test_no_ssh_private_key_inputs(self):
        for name, job, step, match in composite_calls():
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertEqual(sorted(set((step.get("with") or {}).keys()) & SSH_KEY_INPUTS), [])

    def test_removed_composites_have_no_workflow(self):
        names = {path.name for path in WORKFLOWS}
        for removed in sorted(REMOVED):
            with self.subTest(workflow=f"{removed}.yml"):
                self.assertNotIn(f"{removed}.yml", names)

    def test_workflow_templates_checkouts_are_pinned_to_the_version(self):
        """A checkout of the workflow-templates repo itself must use the same tag."""
        seen = 0
        for path in WORKFLOWS:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job_name, job in (doc.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    repo = (step.get("with") or {}).get("repository", "")
                    if step.get("uses", "").startswith("actions/checkout@") and repo.endswith(
                        "/workflow-templates"
                    ):
                        seen += 1
                        with self.subTest(workflow=path.name, job=job_name):
                            self.assertEqual(step["with"].get("ref"), SPEC["version"])
        self.assertGreaterEqual(seen, 1)

    def test_real_calls_have_no_violations(self):
        for name, job, step, match in composite_calls():
            with self.subTest(workflow=name, job=job, composite=match["name"]):
                self.assertEqual(violations(step, match), [])

    def test_violations_catch_a_stale_call(self):
        """Guard the guard: the same check must flag a pre-2.13 call of the old shape."""
        stale_uses = "bigler-webapps/workflow-templates/.github/actions/backup@v2.0.3"
        stale_step = {"uses": stale_uses, "with": {"target": "x", "ssh_private_key": "k"}}
        found = violations(stale_step, USES.match(stale_uses))
        self.assertTrue(any(f.startswith("ref v2.0.3") for f in found), found)
        self.assertTrue(any("ssh key input ssh_private_key" in f for f in found), found)
        self.assertTrue(any("undeclared input ssh_private_key" in f for f in found), found)
        self.assertTrue(any("missing required input ts_oauth_secret" in f for f in found), found)
        removed = "bigler-webapps/workflow-templates/.github/actions/update-server@v2.13.0"
        self.assertTrue(
            any("does not exist" in f for f in violations({"uses": removed}, USES.match(removed)))
        )


if __name__ == "__main__":
    unittest.main()
