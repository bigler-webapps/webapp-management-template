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
