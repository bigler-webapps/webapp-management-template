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
  SSH_HOST: {}
  SSH_PRIVATE_KEY: {}
  SSH_PRIVATE_KEY_ROOT:
    exclude_from_env: true        # only used at bootstrap, never in runtime .env
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
    SSH_HOST: "203.0.113.10"
    SSH_USER: "deploy"
    SSH_PRIVATE_KEY: |
      -----BEGIN OPENSSH PRIVATE KEY-----
      ...
    RESTIC_PASSWORD: "your-strong-password"
    # ...
  staging:
    SSH_HOST: "203.0.113.11"
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

### Server access

| Key | Description | Format |
|---|---|---|
| `SSH_HOST` | Public IP or hostname | `203.0.113.10` |
| `SSH_USER` | Deploy user | `deploy` |
| `SSH_PRIVATE_KEY` | OpenSSH private key for `SSH_USER` | PEM (multi-line) |
| `SSH_PRIVATE_KEY_ROOT` | Root key for bootstrap only | PEM (multi-line) |

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

### App-specific

Apps live in their own repos with their own secrets schemas.

## Secret lifecycle

| Secret class | Rotation cadence | Trigger events |
|---|---|---|
| `SSH_PRIVATE_KEY` | 12 months | Personnel change, compromise |
| `SSH_PRIVATE_KEY_ROOT` | 12 months | Same |
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
