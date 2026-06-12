# Secrets Reference

This template uses the **YAML-source** flow of `sync-secrets`. Real values live
in `secrets.values.yaml` (gitignored). One command pushes them to GitHub
Environment Secrets.

If you adopt a vault later (Proton, Bitwarden, 1Password), the same
`sync-secrets` tool supports vault sources. Migration is local-only.

## Two files, two responsibilities

| File | Content | Committed? |
|---|---|---|
| `secrets.yaml` | **Schema** — which keys exist, optional `source:` for vault | yes |
| `secrets.values.yaml` | **Values** — real secrets per target | no (gitignored) |

## File formats

### `secrets.yaml` (schema)

```yaml
config:
  default_target: production
  target_repo: "your-org/your-infra-repo"
  inventory_path: "inventory/inventory.yaml"

secrets:
  # Deploy/provisioning auth is Tailscale-SSH keyless (Tailscale OAuth, no SSH keys).
  TS_OAUTH_CLIENT_ID:
    exclude_from_env: true        # CI-only, never in runtime .env
  TS_OAUTH_SECRET:
    exclude_from_env: true
  RESTIC_PASSWORD: {}
  # ...
```

The `{}` means: no vault source configured. `sync-secrets --secret-source yaml`
will look up the value in `secrets.values.yaml`.

Optional fields per key:
- `exclude_from_env: true` — don't render this secret into local `.env` files
- `exclude_from_github: true` — don't push to GitHub Environment Secrets
- `source: "proton://..."` — vault source (only used in proton/auto modes)
- `source_template: "proton://...{target}/..."` — target-templated vault path

### `secrets.values.yaml` (values)

```yaml
targets:
  production:
    TS_OAUTH_CLIENT_ID: "tskey-client-..."
    TS_OAUTH_SECRET: "tskey-secret-..."
    RESTIC_PASSWORD: "your-strong-password"
    # ...
  staging:
    TS_OAUTH_CLIENT_ID: "tskey-client-..."
    # ...
```

Pro Target eine Sektion. Keys müssen mit denen aus `secrets.yaml` übereinstimmen.

## Pushing values to GitHub

### Single command

```bash
sync-secrets \
  --server \
  --secret-source yaml \
  --values-file secrets.values.yaml \
  --secret-target production
```

This reads `secrets.yaml` (schema) and `secrets.values.yaml` (values), and pushes
each non-excluded secret to GitHub Environment Secrets for the resolved
environment (taken from `inventory.yaml`).

### Make it the default in project.yaml

If you don't want to type the flags every time, configure them in
`project.yaml` (if you have one) or persist them in your shell history:

```yaml
# project.yaml (optional in this template)
secret_inputs:
  provider: yaml
  values_file: secrets.values.yaml
  target: production
```

Then just:

```bash
sync-secrets --server
```

## Required secrets per environment

### Server access (Tailscale-SSH keyless)

Deploy and provisioning connect over the Tailnet and authenticate via Tailscale
OAuth — there is **no SSH-key path** (retired in `deploy-app v1.11.0`). The deploy
host is derived from `project.yaml` (`environments[env].server` + the tailnet
suffix), not stored as a secret.

| Key | Description |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client ID for the ephemeral CI node (`exclude_from_env`) |
| `TS_OAUTH_SECRET` | Tailscale OAuth client secret (`exclude_from_env`) |

### Backup (Backblaze B2)

| Key | Description | Where to get it |
|---|---|---|
| `RESTIC_REPO_B2` | B2 repo URL | `s3:s3.eu-central-003.backblazeb2.com/<bucket>` |
| `RESTIC_PASSWORD` | Encryption password — **NEVER ROTATE** | Generate once, back up offline |
| `B2_KEY_ID` | B2 Application Key ID | B2 web UI → App Keys |
| `B2_APP_KEY` | B2 Application Key | B2 web UI → App Keys |

### Traefik

| Key | Description |
|---|---|
| `TRAEFIK_DASHBOARD_AUTH` | htpasswd-format auth string — **`$$`-escape required**, see [DASHBOARD.md](DASHBOARD.md) |
| `ACME_EMAIL` | Email for Let's Encrypt |
| `DOMAIN_TRAEFIK` | Traefik dashboard hostname |
| `DOMAIN_KUMA` | Uptime Kuma hostname |

### Uptime Kuma automation (optional)

Only needed if you use the `sync-kuma-notifications` workflow and the
per-app `register-kuma-monitors` step. Configuration as code lives in
`monitoring/notifications.yml` and `monitoring/monitor.yml` (per app).

Kuma's REST API keys are scoped to push/metrics endpoints — monitor and
notification CRUD goes through Socket.IO with a username + password.
Create a dedicated `automation` user in Kuma (Settings → Users) and use
its credentials below. Do not enable 2FA for that user.

| Key | Description |
|---|---|
| `KUMA_URL` | Kuma base URL, e.g. `https://status.example.com` |
| `KUMA_AUTOMATION_USER` | Username of the dedicated automation user |
| `KUMA_AUTOMATION_PASSWORD` | Password for that user |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook (or replace with another notification provider) |

All four are `exclude_from_env: true` — they're only consumed by
workflows, never injected into runtime `.env` files.

### App-specific

Apps live in their own repos with their own secrets schemas.

## Secret lifecycle

| Secret class | Rotation cadence | Trigger events |
|---|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` | 12 months | Personnel change, compromise |
| B2 keys | 12 months | Same |
| `RESTIC_PASSWORD` | **NEVER** | Loss = backups unrecoverable |
| `TRAEFIK_DASHBOARD_AUTH` | 6 months | Compromise |
| App-specific | per provider | per provider |

## Backup of secrets.values.yaml

`secrets.values.yaml` is gitignored — it must not enter the repo. But losing it
means re-deriving all secrets from scratch.

Recommendations:
- Copy to an encrypted USB stick (`gpg --encrypt`, `age`, or LUKS-encrypted drive)
- Or paste into a password manager as a single text item
- Or commit to a **private** separate repo with secret-scanning enabled

Whichever path: test the restore once. Lost-`RESTIC_PASSWORD` = unrecoverable backups.

## Adding new secrets

1. Add the key to `secrets.yaml` (just `KEY_NAME: {}`)
2. Add the value to each target in `secrets.values.yaml`
3. Run `sync-secrets --server --secret-source yaml --values-file secrets.values.yaml --secret-target <target>`
4. Done — value is now in GitHub Environment Secrets

## Removing a secret

Currently `sync-secrets` only adds/updates secrets — it does NOT delete stale
ones. Remove them manually:

```bash
gh secret delete OLD_KEY --env production
```

Then remove the key from `secrets.yaml` and `secrets.values.yaml`.

## Migration to a vault later

If you outgrow the values-file approach:

1. Set up your vault (Proton, Bitwarden, 1Password)
2. Move each value into a vault item
3. Add `source:` or `source_template:` fields to `secrets.yaml`
4. Switch `--secret-source` to `proton` (single vault) or `auto` (yaml fallback)
5. Eventually delete `secrets.values.yaml`

The schema in `secrets.yaml` stays the same — only the value source changes.

Once on the vault, see **[SECRETS_STRUCTURE.md](SECRETS_STRUCTURE.md)** for the
secret-vs-config split (what belongs in `project.yaml app_env` vs the vault), the
Proton namespacing (`<app>/…` vs shared `webapp-management/…`), and a full worked
example.
