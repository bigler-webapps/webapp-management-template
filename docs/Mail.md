# Outbound Mail — Resend (HTTP API)

How apps send mail (invites, password-reset, notifications) in this fleet.

## Why Resend, not SMTP

Some hosting providers (notably **netcup**) **block all outbound SMTP ports**
(25 / 465 / 587). SMTP-based mail then fails silently (connection timeout / 524).
**Resend** delivers over an **HTTP API on port 443** → it bypasses the SMTP block
entirely. Other HTTP-API providers (Postmark, Mailgun) work the same way.

dcm wires this via a pluggable **`EMAIL_PROVIDER`** (django-anymail). Requires
**dcm ≥ 2.18** — verify the app's pin before relying on it (version-gating: a
provider value an older dcm doesn't understand can crash-loop the container).

## Setup

### 0. Resend account + domain (once per tenant)
- Create a Resend account.
- **Verify the sender domain** (e.g. `your-domain.tld`): add the SPF (TXT) + DKIM
  (CNAME/TXT) records Resend gives you — and optionally DMARC — as **DNS records in
  Cloudflare (DNS-only)**. Wait for Resend to show the domain **verified (green)**.
- Create **one API key** — a single account/domain serves all of a tenant's apps.

### 1. Secret → Proton → GitHub
Store the key **shared** in the vault (one key, all apps — see
[SECRETS_STRUCTURE.md](SECRETS_STRUCTURE.md)):

```yaml
# in each app's secrets.yaml:
RESEND_API_KEY:
  source: "proton://<vault>/mail/resend_api_key"
```

Remove dead SMTP secrets (`EMAIL_PASSWORD`, `EMAIL_HOST/PORT/USER`) from
`secrets.yaml` and the vault. Then sync:

```
sync-secrets --server --secret-target production
```

Verify: every line `[OK via proton]`, no `[CLI ERROR]`.

### 2. App config (`project.yaml` `app_env`)
```yaml
app_env:
  EMAIL_PROVIDER: resend
  DEFAULT_FROM_EMAIL: "noreply@your-domain.tld"   # MUST be on the verified domain
```

- `DEFAULT_FROM_EMAIL` **must** be on the verified domain, or Resend rejects every
  send. Per-app senders (`<app>@your-domain.tld`) are fine.
- With dcm ≥ 2.18 this is all the wiring needed — no code change. `EMAIL_HOST/PORT/
  USE_TLS` are irrelevant on the Resend path.

Reference (live): `reimbursements/project.yaml` uses `EMAIL_PROVIDER: resend` +
`DEFAULT_FROM_EMAIL` + `RESEND_API_KEY` in `secrets.yaml`.

## Staging ≠ real sending

Resend HTTP works from staging too — so **without a guard, staging sends real
mail** (quota, accidental sends to real addresses). Decide deliberately:

- **Local** is already `console` (dcm: `IS_LOCAL`).
- **Staging:** set `EMAIL_PROVIDER=console` (or a Mailpit catch-all) so nothing
  real leaves staging. First **verify how dcm resolves `EMAIL_PROVIDER`**
  (settings vs `app_env`/`.env`) and whether `project.yaml` supports per-environment
  `app_env` overrides; otherwise set the staging value via the staging GitHub
  environment.
- **Production:** `resend`.

## Risks / common failures

- **Domain not (fully) verified** → Resend rejects *all* sends. Get DKIM + SPF green
  **before** switching production.
- **From-address on an unverified domain** → reject.
- **Order matters:** domain green + secret synced **first**, *then* flip
  `EMAIL_PROVIDER=resend` — otherwise the first send rejects/crashes.
- **Staging sends real mail** if the guard above is skipped.
- `django-anymail[resend]` must be present — transitive via dcm ≥ 2.18; verify with
  `pip show django-anymail`, else add to `requirements.txt`.

## Verification

- **Production:** trigger a real invite / password-reset → mail arrives; Resend
  dashboard shows `delivered`.
- **Staging:** a send lands in console / Mailpit (no real delivery).
- Backend (read-only): `manage.py shell` → `from django.conf import settings;
  print(settings.EMAIL_BACKEND)` shows the anymail/Resend backend.
- Logs: no more SMTP timeouts / 524.

## Multi-domain / multi-tenant

One Resend account is scoped to its verified domain(s). To send from a different
brand domain, verify that domain in the same account (Resend Pro allows multiple),
or use a separate account per tenant. Each tenant keeps its own Resend account,
domain, API key, and vault item.
