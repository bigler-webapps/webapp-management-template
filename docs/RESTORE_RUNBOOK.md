# Restore Runbook

How to restore application data from backup. Two workflows cover different cases;
both run over Tailscale-SSH and pull from the restic / Backblaze-B2 repo, so **at
least one admin path (Tailnet) must be up**. For the both-paths-down case see
[`BREAK_GLASS.md`](./BREAK_GLASS.md) for the manual recovery path via the provider
console.

## Preconditions

- The target server carries the **`restore` role** in `inventory.yaml` (see
  `inventory.example.yaml`). The workflows' preflight fails fast otherwise
  (`resolve_inventory_targets.py --require-role restore`).
- Backup secrets (`RESTIC_REPO_B2`, `RESTIC_PASSWORD`, `B2_KEY_ID`, `B2_APP_KEY`) plus
  Tailscale OAuth (`TS_OAUTH_CLIENT_ID`/`TS_OAUTH_SECRET`) are provided env-scoped per
  GitHub Environment.
- Cross-server additionally needs repo-level `CROSS_SERVER_TRANSPORT_KEY`.

## Same-server / to-staging / zip — `restore.yml`

`gh workflow run restore.yml` (or the Actions UI). Inputs:

| Input | Meaning |
|---|---|
| `target` | GitHub environment / backup target to read the backup from (e.g. `production`) |
| `app_name` | app to restore |
| `target_env` | where the data goes: `staging`, `zip-file`, or `production` |
| `snapshot_id` | restic snapshot, `latest` by default |
| `confirm_destroy` | must be literally `DESTROY` when `target_env=production` (irreversible); ignored otherwise |

Flow: `preflight` (prod → require the `DESTROY` token) → `prepare` (resolve target via
`resolve_inventory_targets.py --require-role restore`) → `restore` (the pinned `restore`
composite in `YOUR_GITHUB_ORG/workflow-templates`, Tailscale-SSH as `deploy`, restic pull
from B2). Use this for routine same-server restores, on-host staging refreshes, and zip
exports.

## Cross-server — `restore-cross-server.yml`

Moves data **between two servers** (source export → dest import) via the composites. Use
for one-shot app migrations between targets, DR restores between environments, and manual
single-app triggering of the staging-sync pipeline.

| Input | Meaning |
|---|---|
| `source_target` / `dest_target` | source + destination environments/targets (both must carry `restore`) |
| `app_name` | app / compose project prefix |
| `source_env` / `dest_subtarget` | compose env labels → compose project `{app}_{env}` |
| `snapshot_id` | restic snapshot or `latest` |
| `auto_restart` | run `docker compose up -d` on the dest host after import |

Flow: `preflight` (validate both targets carry `restore`; `DESTROY` token for a prod dest)
→ `source_export` → `dest_import`. This is the same machinery the nightly `sync-staging.yml`
uses to pull prod data into staging after each backup.

## Notes

- Both workflows SSH into the target — they presuppose Tailnet is up. Both-paths-down
  recovery is manual (see `BREAK_GLASS.md`).
- `workflow_dispatch` inputs are passed through `env:` (not inline) and regex-validated —
  do not paste shell into the input fields.
- Restores are approval-gated operations in practice: production restores require the
  explicit `DESTROY` token, and the dispatch itself should be gated by your GitHub
  Environment's required-reviewers protection rule.
