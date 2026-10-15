# WMT-DEP-2 — Template on workflow-templates v2.13 and its Python, like webapp-management

Prepared: 2026-10-15. Target repository: `webapp-management-template` (`main`). Follows `WMT-DEP-1`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The template's workflows use the current `workflow-templates` composites (v2.13) in the way `webapp-management` uses them today, so that an estate created from the template works with the composites that exist. Renovate PRs #17 (github-actions incl. Python 3.14.8) and #23 (workflow-templates v2) land or close because their update landed.

## Why

`WMT-DEP-1` left #17 and #23 out: `workflow-templates` v2.13.0 removed the `provision-server`, `sync-ssh-access` and `update-server` composites, its `backup`, `janitor`, `maintenance` and `restore` composites no longer take `ssh_private_key` and require `ts_oauth_client_id`/`ts_oauth_secret`, and `ansible-core<2.18` does not run on a Python 3.14 controller. `webapp-management` has already made these moves; the template has not.

## Tier

Tier 3: CI/deploy workflow templates, secrets layout.

## Operator decisions this Envelope records (2026-10-15)

1. `webapp-management` is the reference: the template follows its current workflow set and composite usage, generalised (no estate-specific hosts, names or secrets values).
2. The template's workflows that call removed composites (`provision-server.yml`, `sync-ssh-access.yml`, `update-server.yml`) are removed; provisioning and updates go through `ansible-provision.yml`, as in `webapp-management`.
3. `backup`, `janitor`, `maintenance`, `restore` and every other composite call move to the v2.13 inputs: Tailscale OAuth (`TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`) instead of SSH private keys. The template's secret declarations and docs follow (SSH private-key secrets removed where nothing uses them any more).
4. All `bigler-webapps/workflow-templates` references sit on one v2.13.x version.
5. The Ansible controller job keeps the Python version `ansible-core<2.18` supports (as `webapp-management` runs it, 3.12); the Python 3.14.8 bump of #17 applies to every other job. A Renovate rule that holds the controller's Python is allowed in this order, so Renovate does not propose 3.14 for it again.

## Scope

1. Decisions 1 to 5 in `.github/workflows/`, the template's secret declaration file and its docs (README, runbooks naming the removed workflows or the SSH-key secrets).
2. The template's own tests (`.github/scripts`, `server_health`) and YAML/JSON parsing stay green; tests that name a removed workflow follow.

## Replaces / removes

- `provision-server.yml`, `sync-ssh-access.yml`, `update-server.yml` and their docs.
- The `ssh_private_key`/`ssh_private_key_root` inputs and the SSH private-key secrets that only they used.

## Non-goals

- Workflows `webapp-management` has that the template does not (pg-upgrade, restore-drill, runner-janitor and others): not added here.
- Any change in `webapp-management` or `workflow-templates`.

## Risks

- Nothing in this repository is applied to a real estate, and it has no CI of its own; a wrong input name shows only when a new estate runs the workflow. Mitigation: each composite call is checked against that composite's `action.yml` inputs at v2.13.x, and against how `webapp-management` calls it.

## Required tests to write

- A structural test that every `uses: bigler-webapps/workflow-templates/...` in the template points at the same v2.13.x version and passes only inputs the composite declares at that version (read from the composite's `action.yml` at that tag, or a checked-in list of its inputs).
- A test that no workflow passes `ssh_private_key` or `ssh_private_key_root` and none references a removed composite.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 recorded in the register row (operator click in session local_1e5f212a-77db-4423-a507-983adf826bc4, 2026-10-15, "Heute noch als WO umsetzen"). After the push, confirm Renovate closes #17 and #23 (and the leftover #11, #18, #19 from WMT-DEP-1 whose updates landed); a PR it rebases instead is an update that did not land.
