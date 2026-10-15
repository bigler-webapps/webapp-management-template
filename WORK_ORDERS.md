# WORK_ORDERS.md — webapp-management-template

Work-order register for this repo. Lightweight directory (not the full orders):
one row per WO with its implementation status. Convention, schema, and maintenance
rules are defined centrally in `webapps/AGENTS.md` → "Work-Order Register".

**Scope note:** this register starts with the 2026-07 tailnet-resilience workstream;
older work is in `git log`.

## Workstream prefixes

| Prefix | Workstream |
|---|---|
| `CI-*` | Workflows / composite actions |

Introduce a new prefix when none fits and add it here. New WOs always get a
prefixed ID; never reuse a bare flat number across workstreams.

## Register

| ID | Titel | Beschreibung | Datum | Status | Commit(s) | Notiz |
|---|---|---|---|---|---|---|
| CI-1 | Convert kuma-sync raw tailnet join | Replace the inline `tailscale/github-action` join in `.github/workflows/kuma-sync.yml` with `tailnet-connect@v2.5.2` (probe-guard + `use-cache` + re-probe-gated retry) so new tenants inherit the resilient join instead of the raw CDN-dependent one | 2026-07-16 | done | 3dd53a7 | Companion of webapp-management `CI-2` (same raw join there was the last regular pkgs.tailscale.com CDN hit after TS-1). Action refs hardcode `bigler-webapps/workflow-templates@vX` per existing template convention (only repo checkouts use the `YOUR_GITHUB_ORG` placeholder). Other stale template action pins (v2.0.x/v2.1.x) are out of scope — owned by the weekly template-backport routine |
| WMT-DEP-1 | Close out the open PRs: Renovate bundle and the pending back-ports | Renovate PRs #11, #17 to #23 in one change; back-ports #12 to #16 reviewed one by one, merged or closed with the reason. Full order [work-orders/WMT-DEP-1.md](work-orders/WMT-DEP-1.md) | 2026-10-15 | planned | | Tier 3 (dependencies, template Terraform/Ansible). Part of the PR close-out round of 2026-10-15 (operator decision 2026-10-15). Envelope by the Infra Expertenchat. Gate #1 approved by the operator 2026-10-15 (click in session local_1e5f212a-77db-4423-a507-983adf826bc4). |
