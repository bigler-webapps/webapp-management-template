# Security Admission Checklist — bigler-webapps

**What this is:** the security bar an app must clear to be admitted into the
bigler-webapps fleet. Derived from `webapp-management/SECURITY_FINDINGS.md`
(S1–S226+, the recurring vulnerability classes), the platform security
architecture (`ARCHITECTURE.md`), and the baseline baked into
`django-core-micha` (dcm) + this template.

**How to read it:**
- **§0** is what you **inherit for free** from dcm/template — your job is to **not loosen it**.
- **§1–§8** are your app's own obligations. `[MUST]` items block admission; `[SHOULD]` items are the hardening pass.
- The living record is `webapp-management/SECURITY_FINDINGS.md` (platform) and each app's `APP_FINDINGS.md`.

Companions: [SECRETS_STRUCTURE.md](SECRETS_STRUCTURE.md), [Mail.md](Mail.md), [ONBOARDING.md](ONBOARDING.md).

---

## §0 — Baseline you inherit (do NOT loosen)

dcm `settings_base` + the template give every app these. Removing/weakening any is a finding.

- DRF `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` — **deny by default**.
- Self-signup **disabled** (`is_open_for_signup → False`); social login **invitation-only** (auto-signup only for pre-verified emails).
- Admin **MFA mandatory** in non-local (`AdminMfaRequiredMiddleware`); recovery codes alone don't satisfy.
- Secure cookies + `SameSite=Lax`, **HSTS** (1y, preload), `SECURE_SSL_REDIRECT`, `SECURE_PROXY_SSL_HEADER`, referrer-policy — all non-local.
- Allauth + DRF **rate limits** on auth flows (login/signup/reset, per-IP **and** per-email).
- WebSocket framework: `BaseSecureConsumer` + `permission_classes_ws` (S112).
- Audit-log actor/request-id middleware; standardized DRF exception handler; sessions `cached_db` on Postgres (no SQLite).
- Pluggable email backend (`EMAIL_PROVIDER`), leaked-credential detection at the edge.

> Rule: if a change touches any §0 mechanism, it needs explicit approval and a finding entry.

---

## §1 — Authorization & access control *(the #1 finding class — A1/A2/A3)*
- [MUST] Every **public** endpoint sets `permission_classes = [AllowAny]` **explicitly** and appears in the app's `PUBLIC_URL_NAMES` allowlist. No implicit/forgotten public endpoints.
- [MUST] Every `perform_create/update/destroy` enforces **object-level** authorization (ownership / membership), not just `IsAuthenticated`.
- [MUST] Multi-tenant: every queryset is **scoped** (`…__organisation=user.org`); writes validate FKs belong to the caller's org (no FK-smuggling).
- [MUST] Serializers use **explicit `fields = [...]`** — never `"__all__"`; audit/owner/org FKs are `read_only=True`.
- [MUST] Site-wide / config mutations (legal pages, settings, roles) gated to admin/editor, not plain `IsAuthenticated`.
- [SHOULD] Query-param filters validated against an enum/range; reject unknown values (no permissive fallback).
- [SHOULD] `GET` is side-effect-free (no DB mutation on read).

## §2 — Authentication & secrets *(A13/A16/A23)*
- [MUST] **No hardcoded secrets**; all from env, with a startup `ImproperlyConfigured` guard for non-local (no dev fallback in prod).
- [MUST] Secrets only via `secrets.yaml` → Proton → GitHub (see [SECRETS_STRUCTURE.md](SECRETS_STRUCTURE.md)); **never a committed `.env`**. Build-time/CI secrets are `target_scope: repo`.
- [MUST] Social login enforces **verified email** (adapter); self-registration stays closed.
- [SHOULD] Token/access-code lookups use HMAC index + `constant_time_compare` (no plaintext, no timing oracle).
- [SHOULD] Separate `CSRF_TRUSTED_*` and `CORS_ALLOWED_ORIGINS` (don't couple the CSRF boundary to CORS).
- [SHOULD] Auth responses are existence-uniform (no user-enumeration via status/shape).

## §3 — Input, files & output *(A4/A8/A11/A19)*
- [MUST] All uploads use dcm `SafeFileField` (magic-byte + MIME + size); `full_clean()` before `save()`. No trust in client extension.
- [MUST] No `dangerouslySetInnerHTML` on user/admin content without sanitisation (DOMPurify/backend allowlist).
- [SHOULD] CSV/Excel exports neutralise formula-injection (cells starting `= + - @`).
- [SHOULD] User-configurable templates (e.g. label/PDF templates) render in a **sandbox** — no template/code injection, no SSRF.
- [SHOULD] No PII/secrets in logs or management-command stdout (redaction filter).

## §4 — Rate-limiting & bot/abuse *(A7/A27)*
- [MUST] App's own sensitive/public-write endpoints (order placement, contact, bulk ops) have **per-user/per-email/per-IP** throttles — not only the inherited auth-flow limits.
- [MUST] Public unauthenticated forms (order, contact) gate with **Cloudflare Turnstile verified server-side** (not just the widget).
- [SHOULD] Token-lookup endpoints (status links) are rate-limited against brute-force.

## §5 — WebSockets (S112) *(A10)* — if the app has consumers
- [MUST] Every `*Consumer` inherits `BaseSecureConsumer` **first** in MRO, declares `permission_classes_ws: [WsPermission]`, and puts post-accept logic in `post_connect()` (never overrides `connect()`).
- [MUST] Intentionally-public/abstract consumers declare `_WS_AUDIT_EXEMPT = "<reason>"`.
- [MUST] Guard URL kwargs that can be `None` (close 1011).

## §6 — Mandatory tests (CI gates)
- [MUST] `test_permission_inventory.py`: asserts the anonymous-reachable endpoints == `PUBLIC_URL_NAMES`; flags un-audited custom `get_permissions()` and `IsAuthenticated`-only views lacking compensating scoping (allowlist with rationale).
- [MUST] `test_ws_inventory.py`: `assert_all_consumers_secure([...]) == []` (if any consumers).
- [MUST] `makemigrations --check` green in CI (no uncommitted migrations).
- [SHOULD] Behavioural tests for the object-level/org-scope guards in §1.

## §7 — Platform / infra fit (deployability)
- [MUST] **No public host ports.** Ingress only via Traefik labels (`websecure`/`tls`) behind the Cloudflare Tunnel. No app-managed public A-record to the origin.
- [MUST] TLS via **Cloudflare Origin Certificates only — no ACME / Let's Encrypt** on the origin.
- [MUST] Deploy via the shared `deploy-app` composite over **Tailscale-SSH (keyless)**; no SSH keys in the repo, no app-managed privileged access.
- [MUST] Non-secret config in `project.yaml` `app_env` (DB host/name/user, EMAIL_PROVIDER, OAuth client IDs, `DEFAULT_FROM_EMAIL`) — not in `secrets.yaml`.
- [MUST] dcm + ui-core-micha pinned to **exact** versions in `requirements.txt` / `package.json` (no `^`/`~`).
- [SHOULD] Security headers: rely on Traefik `sec-headers`/`media-safe-headers`; app sets its own CSP. Private media served via an auth-gated view (edge can't enforce object perms).
- [SHOULD] Health endpoint (`/api/healthz`) + Kuma monitor registered; mail from a **verified** Resend domain ([Mail.md](Mail.md)).

## §8 — CI/CD supply-chain *(A14)*
- [MUST] Third-party GitHub Actions **SHA-pinned** (not `@v1`/`@main`/`@latest`).
- [MUST] Every workflow has top-level `permissions: contents: read` (least privilege); widen explicitly only where needed.
- [MUST] Secrets masked in logs; no plaintext secret echoed.
- [SHOULD] App images built from pinned bases; image tags traceable (SHA), `latest` not relied on for prod.

---

## Admission gate

An app is **admitted** when **all `[MUST]`** items pass and an independent
`sec_reviewer` has signed off. `[SHOULD]` gaps are tracked in the app's
`APP_FINDINGS.md` with owner + severity. Net effect of compliance: the app
inherits the fleet's DDoS/bot defence (Cloudflare), credential-leak detection,
edge rate-limiting, keyless deploy, and audit trail — and adds the
app-specific authZ, input, and abuse controls the edge cannot enforce.

> Most production incidents in this fleet trace to **§1 (authorization)** and
> **§2 (secrets)** — prioritise those.
