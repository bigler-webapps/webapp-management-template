# Secrets & Config — Structure (vault model)

Companion to [SECRETS.md](SECRETS.md). `SECRETS.md` covers the **file formats**,
the baseline `secrets.values.yaml` flow, and the required-secrets tables. **This
document** covers the question those don't: **what belongs where** once you run on
a Proton vault — i.e. the secret-vs-config split, the Proton namespacing, and a
full worked example.

Read this once you've done the "Migration to a vault later" step in `SECRETS.md`
and use `--secret-source proton`.

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
- `secrets.yaml` — server/shared secrets.
- Proton namespace: `webapp-management/<category>/<field>`.

**Tier B — app repos (per app):**
- `project.yaml` — **`app_env:`** (non-secret config) + `environments` (servers/domains/volumes) + `config.target_repo`.
- `secrets.yaml` — the app's **real** secrets (may reference shared `webapp-management/...` items).
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

## 3. `secrets.yaml` field reference (vault model)

```yaml
config:
  target_repo: "<org>/<app-repo>"
  use_project_yaml: true

secrets:
  KEY_NAME:
    source: "proton://<item>/<field>"        # fixed vault path
    # source_template: "proton://webapp-management/server-{target}/..."  # per-server ({target})
    # dev_default: "..."        # local fallback without the vault
    # exclude_from_env: true    # sync to GitHub but never render into the app .env (CI/infra only)
```

- **`source:`** — fixed `proton://<item>/<field>`.
- **`source_template:`** — with `{target}` placeholder for **per-server** secrets (infra tier; resolved by `--secret-target <target>`).
- **`exclude_from_env: true`** — for infra/CI-only secrets (Tailscale, Kuma): synced to GitHub, never written into the app `.env`.

---

## 4. Full worked example — `reimbursements` (a live, simple app)

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
    domains:
      - "reimbursements.<domain>"
    use_traefik: true
  staging:
    server: staging
    domains:
      - "staging-reimbursements.<domain>"
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

### Three cleanly separated groups in `secrets.yaml`
1. **App-owned** (`proton://reimbursements/...`): django, database, mail, api-keys.
2. **Shared** (`proton://webapp-management/...`): social-login, shared-api-keys — **once** in the vault, referenced by every app.
3. **CI/infra** (`exclude_from_env: true`): Tailscale OAuth, Kuma — synced but never in the app `.env`.

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

### Resulting `.env` on the server (what generate-env builds)

```ini
# from project.yaml app_env (Git, plaintext)
DB_NAME=reimbursement-db
DB_USER=reimbursement-db-user
DB_HOST=db
EMAIL_PROVIDER=resend
DEFAULT_FROM_EMAIL=noreply@<domain>
GOOGLE_CLIENT_ID=5852...
MICROSOFT_CLIENT_ID=f107a674-...
MICROSOFT_TENANT_ID=common
# from secrets.yaml → Proton → GitHub (minus exclude_from_env)
DJANGO_SECRET_KEY=***
DB_PASSWORD=***
EMAIL_PASSWORD=***
RESEND_API_KEY=***
OPENAI_API_KEY=***
GOOGLE_SECRET=***
MICROSOFT_SECRET=***
VITE_APP_MUI_LICENSE_KEY=***
# NOT in the .env: TS_OAUTH_*, KUMA_* (exclude_from_env)
```

---

## 5. Migration checklist (per app + webapp-management)

1. Walk every `secrets.yaml` entry: **secret or config?** (table in §0).
2. Move config entries into `project.yaml app_env` (plaintext); **remove** them from
   `secrets.yaml`; drop the corresponding Proton items.
3. Trim `secrets.yaml` to real secrets; point shared ones (OAuth, MUI, CI) at
   `webapp-management/...`.
4. Delete **`SSH_*`** entries (retired — Tailscale-SSH keyless).
5. Mirror the Proton vault: app secrets under `<app>/<category>/<field>`, shared
   under `webapp-management/<category>/<field>`; the shared OAuth app under
   `webapp-management/social-login/`.
6. Re-run `sync-secrets` → **no more `[CLI ERROR]`** (every declared item exists).

## 6. Verification
- `sync-secrets` run: every line `[OK via proton]`, no `[CLI ERROR]`.
- Deploy → `generate-env`: no `[WARN] Missing value for …` for app-relevant keys.
- App social login (Google/Microsoft) works + mail sends → secrets rendered correctly.

---

**Key takeaway:** `[CLI ERROR]` lines don't go away by adding *more* vault items —
they go away because config values **never belonged in the vault**. They go into
`project.yaml app_env` as plaintext. The vault holds only real secrets; the shared
ones (OAuth/MUI/CI) live centrally under `webapp-management/`.
