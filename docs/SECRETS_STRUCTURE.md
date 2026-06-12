# Secrets & Config

The single reference for how secrets and config are structured in this fleet. The
deploy reads secrets from a **Proton Pass vault** via `sync-secrets`
(`--secret-source proton`); non-secret config is plain `project.yaml`. This doc
covers the secret-vs-config rule, the two-tier layout, the `secrets.yaml` field
reference, the infra-tier (`webapp-management`) secret inventory, and a full app
example.

> **Local dev without a vault:** every secret may carry a `dev_default:` fallback,
> so running the app locally does not require `sync-secrets` or vault access.

---

## 0. The one rule that decides everything

> **Is the value secret?** → `secrets.yaml` + Proton Pass.
> **Is it public/config?** → `project.yaml` under `app_env:` (lives in Git, no vault round-trip).

A very common mistake is storing **config** in the vault (client IDs, hosts, tenant
IDs). They are not secret, they fail to fetch when the vault item is missing, and
they add churn. Put them in `project.yaml app_env` as plaintext.

| Secret (→ `secrets.yaml` + Proton) | Not secret (→ `project.yaml app_env`) |
|---|---|
| `*_SECRET` (client secrets) | `*_CLIENT_ID` (client IDs are public) |
| `*_PASSWORD`, API keys | `*_TENANT_ID` (often just `common`) |
| `DJANGO_SECRET_KEY` | Hosts/ports: `DB_HOST`, `EMAIL_HOST`, `EMAIL_PORT` |
| `DB_PASSWORD` | `DB_NAME`, `DB_USER`, `EMAIL_USER` |
| Private keys (`VAPID_PRIVATE_KEY`) | Public keys (`VAPID_PUBLIC_KEY`) |
| `RESEND_API_KEY` | URLs, feature flags (`FRONTEND_BASE_URL`, `EMAIL_PROVIDER`), sender (`DEFAULT_FROM_EMAIL`) |

> **Retired — do NOT store these:** `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`,
> `SSH_PRIVATE_KEY_ROOT`. The platform is **Tailscale-SSH keyless** (since
> `deploy-app v1.11.0`); deploy/provisioning authenticate via Tailscale OAuth, not
> SSH keys. If these still appear in a `secrets.yaml`, delete them.

---

## 1. Two repo tiers, three artifacts

**Tier A — `webapp-management` (infra/platform):** server-level and shared secrets.
- `inventory/inventory.yaml` — the server targets (main-prod, staging, monitoring …).
- `secrets.yaml` — server/shared secrets (full inventory in §5).
- Proton namespace: `<vault>/<category>/<field>`.

**Tier B — app repos (per app):**
- `project.yaml` — **`app_env:`** (non-secret config) + `environments` + `config.target_repo`.
- `secrets.yaml` — the app's **real** secrets (may reference shared `webapp-management` items).
- Proton namespace: `<app>/<category>/<field>`.

---

## 2. How it composes at deploy time

```
project.yaml app_env  ─┐
(plaintext, from Git)  ├─►  generate-env  ─►  final .env  ─►  container
secrets.yaml + Proton ─┘    (on the server)
   │
   └─ sync-secrets: Proton → GitHub Secrets (repo/env),
      only keys declared in secrets.yaml, minus exclude_from_env
```

- `app_env` is injected directly (no vault needed).
- Secrets go through `sync-secrets` (Proton → GitHub) and are rendered by
  `generate-env` only if declared in `secrets.yaml` (`exclude_from_env` stays out).

---

## 3. `secrets.yaml` field reference

```yaml
config:
  default_target: production
  target_repo: "<org>/<repo>"
  inventory_path: "inventory/inventory.yaml"   # infra tier only

secrets:
  KEY_NAME:
    source: "proton://<vault>/<item>/<field>"          # fixed vault path
    # source_template: "proton://<vault>/server-{target}/..."  # per-server ({target}, see §6)
    # target_scope: repo        # push to repo-level GitHub secret, not per-environment (see §6)
    # exclude_from_env: true    # sync to GitHub but never render into the .env (CI/infra only)
    # dev_default: "..."        # local fallback without the vault
```

- **`source:`** — fixed `proton://<vault>/<item>/<field>`.
- **`source_template:`** — with `{target}` placeholder for **per-server** secrets (§6).
- **`target_scope: repo`** — push to a **repo-level** GitHub secret instead of a
  per-environment one. For workflows that have no `environment:` (e.g. `kuma-sync`,
  cross-server restore). Default is per-environment.
- **`exclude_from_env: true`** — infra/CI-only: synced to GitHub, never written into the app `.env`.
- **`dev_default:`** — local fallback when not running against the vault.

---

## 4. Full worked example — `reimbursements` (app tier)

### `project.yaml` — everything non-secret

```yaml
project_name: "reimbursements"
version: "0.1.0"
container_prefix: "reimbursements"
image_name: "ghcr.io/<org>/reimbursements-backend"
root_module: "backend"

environments:
  production:
    server: main-prod          # resolves {target}/{server} in secrets.yaml
    domains: ["reimbursements.<domain>"]
    use_traefik: true
  staging:
    server: staging
    domains: ["staging-reimbursements.<domain>"]
    use_traefik: true
  local:
    domains: ["localhost", "127.0.0.1"]
    use_traefik: false
    web_port: 8028
    frontend_port: 5128
    db_port: 5428
    redis_port: 6328

app_env:                       # ── NON-SECRET, lives in Git ──
  DB_NAME: reimbursement-db
  DB_USER: reimbursement-db-user
  DB_HOST: db
  EMAIL_PROVIDER: resend
  DEFAULT_FROM_EMAIL: "noreply@<domain>"
  GOOGLE_CLIENT_ID: "5852...apps.googleusercontent.com"   # public
  MICROSOFT_CLIENT_ID: "f107a674-..."                      # your shared OAuth app
  MICROSOFT_TENANT_ID: common
```

### `secrets.yaml` — real secrets only

```yaml
config:
  target_repo: "<org>/reimbursements"
  use_project_yaml: true

secrets:
  # ── app-owned secrets: proton://reimbursements/... ──
  DJANGO_SECRET_KEY:
    source: "proton://reimbursements/django/secret_key"
    dev_default: "local-dev-secret-key"
  DB_PASSWORD:
    source: "proton://reimbursements/database/password"
    dev_default: "reimbursements-db-password"
  EMAIL_PASSWORD:
    source: "proton://reimbursements/mail/password"
  RESEND_API_KEY:
    source: "proton://reimbursements/mail/resend_api_key"
  OPENAI_API_KEY:
    source: "proton://reimbursements/api-keys/ai_ocr"

  # ── shared: proton://webapp-management/... (one source, all apps) ──
  GOOGLE_SECRET:
    source: "proton://webapp-management/social-login/google_client_secret"
  MICROSOFT_SECRET:
    source: "proton://webapp-management/social-login/azure_client_secret"
  VITE_APP_MUI_LICENSE_KEY:
    source: "proton://webapp-management/shared-api-keys/mui_license"

  # ── CI/infra-only: exclude_from_env (never in the app .env) ──
  TS_OAUTH_CLIENT_ID:
    source: "proton://webapp-management/ci-tokens/ts_oauth_client_id"
    exclude_from_env: true
  TS_OAUTH_SECRET:
    source: "proton://webapp-management/ci-tokens/ts_oauth_secret"
    exclude_from_env: true
  KUMA_AUTOMATION_USER:
    source: "proton://webapp-management/monitoring/kuma_automation_user"
    exclude_from_env: true
  KUMA_AUTOMATION_PASSWORD:
    source: "proton://webapp-management/monitoring/kuma_automation_password"
    exclude_from_env: true
```

### Resulting Proton vault layout

```
reimbursements/                    ← app-owned namespace
  ├─ django/secret_key
  ├─ database/password
  ├─ mail/password
  ├─ mail/resend_api_key
  └─ api-keys/ai_ocr
webapp-management/                 ← shared/central (for ALL apps)
  ├─ social-login/google_client_secret
  ├─ social-login/azure_client_secret
  ├─ shared-api-keys/mui_license
  ├─ ci-tokens/ts_oauth_client_id
  ├─ ci-tokens/ts_oauth_secret
  └─ monitoring/kuma_automation_*
```

---

## 5. Infra tier — `webapp-management` secret inventory

These live in `webapp-management/secrets.yaml`, not in app repos. Grouped by category
as the template ships them. Paths shown relative to your vault (`proton://<vault>/…`).

**Per-server connectivity** — `source_template` with `{target}`, `exclude_from_env`:
| Key | Purpose |
|---|---|
| `TAILSCALE_AUTH_KEY` | Tailnet join key for the server node |
| `CLOUDFLARE_TUNNEL_TOKEN` | cloudflared tunnel token (**per server** — never copy across servers) |

**Backup (Backblaze B2)** — per-server:
| Key | Purpose |
|---|---|
| `RESTIC_REPO_B2` | restic repo URL |
| `RESTIC_PASSWORD` | **NEVER rotate** — loss = backups unrecoverable; back up offline |
| `B2_KEY_ID` / `B2_APP_KEY` | B2 application key |

**Cross-server restore:**
| Key | Purpose |
|---|---|
| `CROSS_SERVER_TRANSPORT_KEY` | repo-scoped (`target_scope: repo`), `exclude_from_env` |

**Cloudflare API:**
| Key | Purpose |
|---|---|
| `CLOUDFLARE_API_TOKEN` | DNS / tunnel management |
| `CLOUDFLARE_ACCESS_API_TOKEN` | CF Access (Zero Trust) |
| `CLOUDFLARE_ACCOUNT_ID` | account id |

**CI tokens (Tailscale OAuth):**
| Key | Purpose |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` | ephemeral deploy node |
| `TS_PROVISION_OAUTH_CLIENT_ID` / `TS_PROVISION_OAUTH_SECRET` | dedicated provisioning node |
| `TAILSCALE_MGMT_AUTH_KEY` | management Tailnet key |

**Traefik:**
| Key | Purpose |
|---|---|
| `TRAEFIK_DASHBOARD_AUTH` | htpasswd string — **`$$`-escape** the bcrypt `$` signs (else docker compose treats them as vars) |
| `DOMAIN_TRAEFIK` / `DOMAIN_KUMA` | per-server dashboard / status hostnames |

> `ACME_EMAIL` is **not used** — TLS is via Cloudflare Origin Certificates, not Let's Encrypt.

**Cloudflare Origin Certificates** — one pair per domain, `exclude_from_env`:
| Key | Purpose |
|---|---|
| `ORIGIN_CERT_<DOMAIN_SLUG>` / `ORIGIN_KEY_<DOMAIN_SLUG>` | per-domain cert+key, written to `certs/` by the deploy role |

**Monitoring / Uptime Kuma** — `exclude_from_env`:
| Key | Purpose |
|---|---|
| `KUMA_URL`, `KUMA_AUTOMATION_USER`, `KUMA_AUTOMATION_PASSWORD` | Kuma automation user (no 2FA) |
| `DISCORD_WEBHOOK_URL` | notification channel |

**Kuma sync (repo-scoped GitHub App)** — `target_scope: repo`, `exclude_from_env`:
| Key | Purpose |
|---|---|
| `KUMA_SYNC_APP_ID` / `KUMA_SYNC_APP_PRIVATE_KEY` | GitHub App for the central `kuma-sync` workflow |

**Self-hosted GitHub runner (GitHub App)** — sync with `--secret-target runners --github-environment runners`:
| Key | Purpose |
|---|---|
| `GH_RUNNER_APP_ID`, `GH_RUNNER_APP_INSTALLATION_ID`, `GH_RUNNER_APP_PRIVATE_KEY`, `GH_RUNNER_SHA256` | runner registration |

**Terraform Cloud:**
| Key | Purpose |
|---|---|
| `TF_CLOUD_TOKEN` | TFC API token |

---

## 6. Per-server secrets: the `{target}` pattern

Per-server secrets use `source_template` with a `{target}` placeholder, so **one
declaration covers every server** — no per-server duplication:

```yaml
RESTIC_PASSWORD:
  source_template: "proton://<vault>/server-{target}/restic_password"
```

`{target}` is resolved from:
- **Infra tier:** the `--secret-target <target>` flag, e.g.
  `sync-secrets --server --secret-target staging` → `proton://<vault>/server-staging/restic_password`.
- **App tier:** `environments[env].server` in `project.yaml` (e.g. `server: main-prod`).

A plain `source:` (no `{target}`) is a single **shared** item used by all targets.

`target_scope: repo` pushes to a **repo-level** GitHub secret rather than a
per-environment one — needed for workflows that run without an `environment:`
(e.g. `kuma-sync`, cross-server restore).

---

## 7. Rotation & lifecycle

| Secret class | Cadence | Trigger |
|---|---|---|
| `RESTIC_PASSWORD` | **NEVER** | Loss = backups unrecoverable |
| `TS_OAUTH_*` / `TS_PROVISION_*` / B2 keys / `TF_CLOUD_TOKEN` | 12 months | Personnel change, compromise |
| `TRAEFIK_DASHBOARD_AUTH` | 6 months | Compromise |
| App `*_SECRET` / `*_PASSWORD` | per provider | per provider |

---

## 8. Migration checklist (per app + webapp-management)

1. Walk every `secrets.yaml` entry: **secret or config?** (table in §0).
2. Move config entries into `project.yaml app_env` (plaintext); **remove** them from
   `secrets.yaml`; drop the corresponding Proton items.
3. Trim `secrets.yaml` to real secrets; point shared ones (OAuth, MUI, CI) at
   `webapp-management/...`.
4. Delete **`SSH_*`** entries (retired — Tailscale-SSH keyless).
5. Mirror the Proton vault: app secrets under `<app>/<category>/<field>`, shared/infra
   under `webapp-management/<category>/<field>`; the shared OAuth app under
   `webapp-management/social-login/`.
6. Re-run `sync-secrets` → **no more `[CLI ERROR]`** (every declared item exists).

## 9. Removing a secret

`sync-secrets` only **adds/updates** — it does not delete stale GitHub secrets. Remove
them manually, then drop the key from `secrets.yaml`:

```
gh secret delete OLD_KEY --env <environment>
```

## 10. Verification
- `sync-secrets` run: every line `[OK via proton]`, no `[CLI ERROR]`.
- Deploy → `generate-env`: no `[WARN] Missing value for …` for app-relevant keys.
- App social login (Google/Microsoft) works + mail sends → secrets rendered correctly.

---

**Key takeaway:** `[CLI ERROR]` lines don't go away by adding *more* vault items —
they go away because config values **never belonged in the vault**. They go into
`project.yaml app_env` as plaintext. The vault holds only real secrets; the shared
ones (OAuth/MUI/CI) live centrally under `webapp-management/`.
