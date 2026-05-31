# TENANT_ONBOARDING_GUIDE.md — App-Repo Onboarding (Claude-Agent Edition)

This guide is optimized for a Claude-agent (sec_reviewer / backend_coder / Plan
agent) executing the onboarding **autonomously or semi-autonomously**. Each
section has explicit pre-conditions, deterministic commands, verification
queries, and an explicit pause-point if human input is required.

> **Audience:** Claude-agent or human-operator who is **about to create a new
> tenant repo** from this template and bring it to first-deploy-readiness.
>
> **Companion doc:** `bigler-webapps/webapp-management-template/docs/ONBOARDING.md`
> covers the **infrastructure-side** (Traefik, Kuma, server provisioning).
> This file covers the **app-side**.
>
> **Time budget:** 30–60 min hands-on if all prerequisites are in place.
> 3–5 h if infrastructure also needs to be set up first.

---

## 0. Pre-conditions (verify BEFORE Step 1)

The agent MUST verify each of these before starting. Skip none.

### 0.1 Platform-side prerequisites

| Check | Verification command | Expected |
|---|---|---|
| Plattform `webapp-management` ist deployed | `gh api repos/<owner>/webapp-management --jq .default_branch` | `main` |
| Tailscale tailnet exists + agent has access | `tailscale status --json \| jq -r .Self.HostName` | the host name, not error |
| Target Hetzner server exists | DNS-A-record resolves to its IP | IP returned |
| Cloudflare zone exists for the domain | `gh secret list --env production -R <owner>/webapp-management \| grep CF_` | `CF_TUNNEL_TOKEN`, `CF_ZONE_ID`, etc. populated |
| Proton-Pass entries for the tenant exist | Check Proton-Pass UI for `Projekt <Tenant-Name>` vault | Vault present, key Categories: Database / Django / API-Keys / Infrastructure-Access / Mail |

**Pause if any check fails.** The platform must be ready before app-onboarding.

### 0.2 Authorization to act

| Permission | How to verify |
|---|---|
| GitHub: create repo from template | `gh api user --jq .login` returns your login + you have write on `bigler-webapps` org |
| GitHub: create Environments | Repo Admin role required |
| Proton-Pass: read access to the tenant vault | Open Proton Pass, navigate to vault |
| Tailscale: ACL-write on tailnet (to add `tag:server-<tenant>`) | `tailscale auth -h` shows admin commands |
| Cloudflare: API-Token with `Zone:Edit` for the tenant zone | `curl -H "Authorization: Bearer $CF_API_TOKEN" "https://api.cloudflare.com/client/v4/zones?name=<domain>" \| jq` returns zone |

**Pause if any permission is missing.** Ask the human-operator to grant before proceeding.

### 0.3 Required local tools

```bash
which git gh pnpm python node uv docker docker-compose proton-pass-cli || echo "MISSING_TOOL"
```

Expected: all tools present. `proton-pass-cli` is the official Proton CLI.

---

## 1. Create the tenant repo

### 1.1 Decide naming

The agent MUST settle the following before creating:

- **Tenant slug:** `<lowercase-no-spaces>` — e.g. `acme-shop`. Used as project_name, domain prefix, DB-name, etc.
- **Display name:** human-readable, e.g. "ACME Shop".
- **Target domain:** e.g. `acme-shop.bigler-consult.ch` (staging) and `app.acme-shop.com` (production).
- **Owning org / GitHub:** typically `bigler-webapps` (org) or `MichaBigler` (personal).

### 1.2 Create from template

```bash
gh repo create <owner>/<tenant-slug> \
  --template bigler-webapps/webapp-template \
  --private \
  --description "Tenant: <Display name>"
```

### 1.3 Clone + first orientation

```bash
git clone git@github.com:<owner>/<tenant-slug>.git
cd <tenant-slug>
git checkout -b develop main && git push -u origin develop
gh api repos/<owner>/<tenant-slug>/branches/main --jq .protection || echo "main not protected yet"
```

### 1.4 Configure branch protection (recommended)

```bash
gh api -X PUT repos/<owner>/<tenant-slug>/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null
}
JSON
```

Branch protection on `main` is non-blocking on the agent's first-deploy flow but
forces release-promotion through PRs (develop → main).

**VERIFY:** `git branch --show-current` returns `develop`. Open the repo in GitHub
to confirm `main` and `develop` both exist.

---

## 2. Configure `project.yaml`

### 2.1 Adapt the template

```bash
$EDITOR project.yaml
```

Replace placeholders. Reference: existing app `jg-ferien/project.yaml` is a good
template — it pins server-resolution-via-project-yaml correctly.

Minimum config:

```yaml
project_name: <tenant-slug>
environments:
  staging:
    server: staging                # resolves to staging.tail990d7f.ts.net via Tailscale
    domain: <tenant-slug>.bigler-consult.ch
    db_host_port: <unique-port>    # pick from 5430-5499 not yet used
    web_port: <unique-port>        # pick from 8100-8199 not yet used
  production:
    server: prod-1                 # or whatever the production server name is
    domain: app.<tenant-domain>
    db_host_port: <unique-port>
    web_port: <unique-port>
central_services:
  kuma:
    host: kuma.bigler-consult.ch
```

**VERIFY:** `python -c "import yaml; print(yaml.safe_load(open('project.yaml')))"` parses.

### 2.2 Pick unique ports

The agent MUST pick ports not yet used by other tenants. Check via:

```bash
for app in hram jg-ferien kerzenziehen innoservice survey_app survey_contact_app reimbursements; do
  grep -E "DB_HOST_PORT|WEB_PORT" "/c/Users/Micha Bigler/Documents/webapps/$app/secrets.yaml" 2>/dev/null | grep -oE '[0-9]+' | head -2
done
```

Pick db_host_port and web_port outside the union of the above.

---

## 3. Configure `secrets.yaml`

### 3.1 Replace template placeholders

The template's `secrets.yaml` defines the SCHEMA. Each `source:` or `source_template:`
URL points into Proton-Pass. The agent MUST update these to point to the new
tenant's Proton vault.

Quick path: copy `jg-ferien/secrets.yaml` and find/replace:
- `proton://Projekt HRAM/` → `proton://Projekt <Tenant-Name>/`
- (any other tenant-specific entry)

**Critical secrets that MUST exist in Proton:**

| Proton path | Why |
|---|---|
| `Projekt <Tenant>/Database/{username,password,database_name,host}` | DB connection |
| `Projekt <Tenant>/Django/{secret_key,debug}` | Django settings + S40-assertion |
| `Projekt <Tenant>/Mail/{host,port,user,password}` | Email backend (S40-assertion) |
| `Projekt <Tenant>/API-Keys/<as-needed>` | App-specific API keys |
| `Projekt Webapp-Management/Infrastructure-Access {server}/ssh_*` | SSH per-server-template (cross-tenant) |
| `Projekt Webapp-Management/Cloudflare API/ts_oauth_*` | Tailscale CI auth (cross-tenant) |
| `Projekt Webapp-Management/Traefik/kuma_automation_*` | Kuma sync (cross-tenant) |
| `Projekt Webapp-Management/Kuma-Access/ssh_*` | Kuma SSH-Tunnel (cross-tenant) |

**Critical secrets that should NOT be in `secrets.yaml`** (deprecated/removed in 2026):
- `SYNC_SHARED_SECRET` — removed via S71 cleanup. Do not re-add.

### 3.2 Sync to local `.env.local`

```bash
sync-secrets --secret-source proton --secret-target local --values-file .env.local
```

This reads Proton-Pass and writes a local `.env.local`. The file is gitignored.

**VERIFY:** `.env.local` contains the declared keys, no value is empty (except deliberate `dev_default`s).

---

## 4. Initial backend setup

### 4.1 Bump dependencies if drifted from template

```bash
# Verify current pin matches latest released versions
grep django-core-micha backend/requirements.txt    # should be 2.13.3 or later
grep ui-core-micha frontend/package.json           # should be 2.4.3 or later
```

If outdated:
- Update `backend/requirements.txt`: `django-core-micha==<latest>`
- Update `frontend/package.json`: `"@micha.bigler/ui-core-micha": "<latest>"`
- Run `cd frontend && pnpm install --lockfile-only`

**Check latest released versions:**
```bash
# dcm latest
gh api repos/bigler-webapps/django-core-micha/releases/latest --jq .tag_name
# ui-core latest
pnpm view @micha.bigler/ui-core-micha version
```

### 4.2 Verify `backend/backend/urls.py` matches S106-pattern

```bash
grep -E "accounts/.*allauth.urls" backend/backend/urls.py
# Expected: NO match — accounts/ mount removed per S106
```

If a match exists, remove the line.

### 4.3 Run local migrations + test boot

```bash
cd backend
uv pip install -e .
python manage.py migrate
python manage.py createsuperuser     # interactive
python manage.py runserver 0.0.0.0:<web_port>
# Visit http://localhost:<web_port>/admin/ — should redirect or show admin
```

Stop the server. Frontend test:
```bash
cd ../frontend
pnpm install
pnpm dev
# Open http://localhost:5173 — should show login/landing
```

**PAUSE:** Manual visual smoke-test that login flow works locally. The agent can skip if browser-driven testing is not available.

---

## 5. Configure GitHub Environments

### 5.1 Create environments

```bash
gh api -X PUT repos/<owner>/<tenant-slug>/environments/staging --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON

gh api -X PUT repos/<owner>/<tenant-slug>/environments/production --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON

gh api -X POST repos/<owner>/<tenant-slug>/environments/staging/deployment-branch-policies \
  --input - <<'JSON'
{"name": "develop"}
JSON

gh api -X POST repos/<owner>/<tenant-slug>/environments/production/deployment-branch-policies \
  --input - <<'JSON'
{"name": "main"}
JSON
```

### 5.2 Push secrets

```bash
sync-secrets --secret-source proton --secret-target staging
sync-secrets --secret-source proton --secret-target production
```

**VERIFY:** Each environment-secrets count matches `secrets.yaml` declared count.
```bash
gh api repos/<owner>/<tenant-slug>/environments/staging/secrets --jq '.total_count'
```

---

## 6. Server-side provisioning

The agent MUST verify the target server is already provisioned (by
`webapp-management/.github/workflows/provision-server.yml`) BEFORE proceeding.

### 6.1 Server pre-check

```bash
# Verify Tailscale ACL allows tag:ci-deploy → tag:server-<tenant>
tailscale status | grep <tenant-slug>
# If missing: add ACL entry in tailscale.com admin → ACL → Edit
```

If the server doesn't exist yet, **PAUSE** and execute platform-side onboarding
(`webapp-management-template/docs/ONBOARDING.md` Steps 3–9) first.

### 6.2 Run sync-ssh-access for the new tenant

```bash
cd /c/Users/Micha\ Bigler/Documents/webapps/webapp-management
gh workflow run sync-ssh-access.yml --field target=staging
gh run watch
```

### 6.3 Register the app's directory on the server

(`webapp-management` provisions the apps' directory under `/srv/apps/<tenant>/`
the first time the deploy-app composite runs — no manual server action needed.)

---

## 7. Cloudflare-Tunnel + DNS configuration

### 7.1 DNS

Add CNAME records pointing the tenant's app domain to the Cloudflare Tunnel.

```bash
CF_API_TOKEN=$(proton-pass read "Projekt Webapp-Management/Cloudflare API/api_token")
ZONE_ID=$(curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=bigler-consult.ch" | jq -r '.result[0].id')

# Staging
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
  --data '{"type":"CNAME","name":"<tenant-slug>","content":"<tunnel-uuid>.cfargotunnel.com","proxied":true}'
```

Replace `<tunnel-uuid>` with the UUID from
`webapp-management/secrets.yaml` `CF_TUNNEL_ID`.

### 7.2 Cloudflare Origin Cert

Origin certs are platform-shared. Verify the cert already covers the new
subdomain or the wildcard. Typical webapp-management cert covers
`*.bigler-consult.ch` so a new staging subdomain just works.

For production with a different apex domain: generate a new Origin Cert via
Cloudflare → SSL/TLS → Origin Server → Create Certificate, then add the new
cert to `webapp-management/origin-certs.yml`.

**PAUSE:** Manual step. The platform-operator updates origin-certs.yml.

### 7.3 Update tunnel ingress

In `webapp-management/cloudflared/config.yml` add an entry:

```yaml
ingress:
  - hostname: <tenant-slug>.bigler-consult.ch
    service: https://traefik:443
    originRequest:
      noTLSVerify: false
      caPool: /etc/ssl/certs/origin-ca.crt
  ...
```

Then redeploy webapp-management cloudflared:
```bash
cd /c/Users/Micha\ Bigler/Documents/webapps/webapp-management
docker compose up -d --force-recreate cloudflared
# OR:
gh workflow run deploy-traefik.yml --field target=staging
```

---

## 8. First deploy

### 8.0 (Optional) Self-hosted Runner-Pool — opt-in

> **STOP — Pause-Point. This step is OPTIONAL and the default is to SKIP it.**
> By default a new tenant uses **GitHub-hosted** runners (`ubuntu-latest`).
> Only proceed if the operator has **explicitly decided** to move this tenant's
> CI/deploy onto the shared **self-hosted `netcup` pool** (cost reduction).
> If unsure, **skip to 8.1** — the tenant deploys fine on GitHub-hosted runners
> and can be switched later with zero data impact.

**Pre-condition (verify BEFORE switching):** a runner host must already be
provisioned and `Idle` in the org `runners` group, otherwise jobs pointed at
`netcup` will hang `queued` forever.

```bash
# Expected: at least one self-hosted runner with label "netcup" in status "online".
# Requires org-admin scope (or a fine-grained PAT with the "Self-hosted runners"
# org permission). A repo-write-only onboarding token gets HTTP 403 here — that
# is NOT "no runners". On 403, verify manually: org Settings → Actions → Runners.
gh api orgs/<owner>/actions/runners \
  --jq '.runners[] | select(.status=="online") | .name + " " + ([.labels[].name] | join(","))' \
  || echo "SCOPE_ERROR (likely 403, not admin) — verify runners manually in org Settings → Actions → Runners"
```

**Pause if the pre-condition fails.** Provision a runner host first — see
`bigler-webapps/webapp-management/RUNNER_HOST_BOOTSTRAP.md` — then resume here.

Opt-in switches three workflows in the **tenant repo** to the pool (authoritative
YAML: `RUNNER_HOST_BOOTSTRAP.md §5`):

```yaml
# .github/workflows/ci.yml  (reusable App CI)
with:
  runs-on: netcup            # single label; default would be 'ubuntu-latest'
  run-backend: true
  run-frontend-tests: true

# .github/workflows/staging-health.yml
with:
  runs-on: netcup

# .github/workflows/main.yml  — Deploy-Job
runs-on: [self-hosted, netcup]   # multi-label array (not the reusable input)
```

**VERIFY:** after pushing the workflow change, the next run executes on the pool.
```bash
# NOTE: use the REST jobs endpoint — `gh run view --json jobs` does NOT expose
# runner_name, so it would always print "?" and give a false green.
RUN_ID=$(gh run list -R <owner>/<tenant-slug> --limit 1 --json databaseId --jq '.[0].databaseId')
gh api repos/<owner>/<tenant-slug>/actions/runs/${RUN_ID}/jobs \
  --jq '.jobs[] | .name + " → " + (.runner_name // "?")'
# Expected: runner_name is a netcup slot (e.g. <host>-slot1), NOT a GitHub-hosted runner.
```

> Skipping this step is fully supported and leaves the tenant on GitHub-hosted
> runners. No other onboarding step depends on this choice.

### 8.1 Push develop → trigger Deploy App

```bash
cd /c/Users/Micha\ Bigler/Documents/webapps/<tenant-slug>
# Make a no-op commit to trigger CI
git commit --allow-empty -m "chore: trigger first deploy"
git push origin develop
gh run watch
```

### 8.2 Verify deployment

```bash
# Container running?
ssh deploy@staging.tail990d7f.ts.net "docker ps --filter name=<tenant-slug> --format '{{.Names}} {{.Status}}'"

# Smoke-test HTTPS endpoint
curl -I https://<tenant-slug>.bigler-consult.ch/api/auth/_allauth/browser/v1/auth/session
# Expected: HTTP/2 200 (or 401 if no session) — NOT 502 or 503
```

### 8.3 Security baseline checks

The agent MUST execute these post-deploy security verifications:

```bash
DOMAIN="<tenant-slug>.bigler-consult.ch"

# S6: sec-headers present
curl -sI "https://$DOMAIN" | grep -iE "strict-transport-security|x-frame-options|x-content-type-options|referrer-policy"
# Expected: 4 headers present

# S106: /accounts/ NOT exposed
curl -sIw "%{http_code}\n" "https://$DOMAIN/accounts/login/" -o /dev/null
# Expected: 404

# S19: admin requires MFA in non-local env
curl -sIw "%{http_code}\n" "https://$DOMAIN/admin/" -o /dev/null
# Expected: 302 (redirect to login). After superuser-login without MFA: 403.

# S70 deny-by-default
curl -sIw "%{http_code}\n" "https://$DOMAIN/api/users/" -o /dev/null
# Expected: 401 or 403 — NOT 200

# /media/ media-safe-headers (if app has uploads)
curl -sI "https://$DOMAIN/media/some-file.pdf" 2>&1 | grep -iE "content-disposition|x-content-type-options"
# Expected: Content-Disposition: attachment; X-Content-Type-Options: nosniff
```

If any check fails, **PAUSE** and investigate. Do NOT proceed to production deploy.

---

## 9. Monitoring registration

```bash
cd /c/Users/Micha\ Bigler/Documents/webapps/<tenant-slug>
# register-kuma-monitors composite reads project.yaml + creates Kuma monitors
gh workflow run main.yml --field environment=staging
# Or: monitor-only run via webapp-management
cd /c/Users/Micha\ Bigler/Documents/webapps/webapp-management
gh workflow run sync-kuma-notifications.yml
```

**VERIFY:** Open Kuma dashboard at `https://kuma.bigler-consult.ch` and confirm
a new monitor for `<tenant-slug>` appears.

---

## 10. Post-staging-soak gates

> Two independent gates fire after the staging burn-in window. Both require a
> **≥ 7-day soak period** with the staging deploy stable AND Tailscale-SSH
> proven as the primary access path. Do **NOT** combine these into one big-bang
> cutover — production-promotion and SSH-lockdown are orthogonal risks.

### 10.1 Soak-period acceptance criteria (gate for 10.2 and 10.3)

The agent MUST verify ALL of the following before either lockdown or
production-promotion proceeds. The clock starts on the first successful
staging deploy (Section 8). Earliest gate-pass: 7 calendar days later.

```bash
# Tailscale uptime ≥ 99% over the soak window (read Tailscale admin → Devices)
# Manual check — record `last_seen` per device, compute downtime ratio.

# Zero Tailscale outages ≥ 5 minutes in the soak window
# Check via: tailscale status --json + tailscale debug netcheck history
# (or read Tailscale admin → Logs if Tailscale Business)

# At least 3 successful deploys via Tailscale-SSH path in the soak window
gh run list --workflow main.yml --branch develop --status success --limit 10 \
  --json conclusion,createdAt,headBranch \
  --jq '[.[] | select(.headBranch=="develop")] | length'
# Expected: ≥ 3

# Public-SSH-via-22 has NOT been used as fallback during soak
# Manual check — operator's own action log; OR audit `/var/log/auth.log` on the server
ssh deploy@<server> "grep 'Accepted publickey' /var/log/auth.log | tail -20"
# Expected: source IPs are all 100.x.x.x (Tailscale CGNAT range) — no public IPs
```

If ANY criterion fails, the soak resets. Restart the 7-day clock from the date
the issue was resolved.

**PAUSE:** Manual human review of the soak evidence. The agent must NOT proceed
to 10.2 or 10.3 without explicit operator approval recording the soak-pass.

### 10.2 Public-SSH lockdown (hardening — after 10.1 passes)

> **Why this is gated**: the platform's `provision-server.yml` installs UFW
> with `tcp/22` open BY DESIGN — that's the break-glass path while Tailscale
> is unproven. Closing it on day 1 would lock you out of the server the moment
> Tailscale has a hiccup (ACL drift, OAuth token expiry, tailscaled restart
> loop). The 7-day soak gives Tailscale a chance to fail loudly while you can
> still recover.

Once 10.1 is signed off, lock down public-SSH:

```bash
# 1. Pre-flight: confirm Tailscale-SSH STILL works RIGHT NOW (don't trust soak alone)
ssh deploy@<tenant-slug>-prod.tail990d7f.ts.net "hostname && date"
# Expected: server hostname + current date — NO password prompt

# 2. Pre-flight: confirm you can reach Hetzner Rescue Console
#    (Hetzner Cloud → server → Console → log in once → log out)
#    This is the break-glass-of-the-break-glass.

# 3. On the server, deny tcp/22 from public:
ssh deploy@<tenant-slug>-prod.tail990d7f.ts.net "sudo ufw delete allow 22/tcp && sudo ufw status verbose"
# Expected status: 22/tcp not in the rules list; default policy `deny (incoming)`

# 4. Harden sshd to refuse password auth (defence-in-depth — Tailscale-SSH
#    uses SSO, not passwords, but keep keys-only on the public bind too if
#    it ever gets reopened during break-glass):
ssh deploy@<tenant-slug>-prod.tail990d7f.ts.net "sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && sudo systemctl reload sshd"

# 5. VERIFY from a non-Tailscale network (e.g. phone hotspot):
ssh -o ConnectTimeout=5 deploy@<server-public-ip>
# Expected: timeout or "Connection refused" — NOT a login prompt
```

**Reversal procedure**: see `webapp-management-template/docs/BREAK_GLASS.md`
Scenario 2 for re-opening tcp/22 if Tailscale fails later.

### 10.3 Develop → Main promotion (production cutover — after 10.1 passes)

```bash
git checkout main
git pull --ff-only origin main
git merge --no-ff develop
git push origin main
# OR via PR:
gh pr create --base main --head develop --title "Release: develop → main"
```

Production deploy fires automatically on push to main.

**Repeat Section 8.3 security checks** against the production domain.

---

## 11. APP_FINDINGS.md — initialize for the new tenant

```bash
$EDITOR APP_FINDINGS.md
# Replace `<tenant-name>` with the actual tenant slug
# Update the `<APP>-NEW-` prefix convention to match tenant tag (e.g. ACME-NEW)
```

Add the tenant to the central `webapp-management/SECURITY_FINDINGS.md` overview
tables as a tracked repo.

---

## 12. Decision-points & Pause-points summary

| Step | Pause-Reason | Resume-Trigger |
|---|---|---|
| 0.1 | Platform-side infra not deployed | After platform onboarding completes |
| 0.2 | Authorization missing | After human grants permissions |
| 1.1 | Naming uncertain | User confirms slug + domain |
| 3.1 | Proton-Pass entries not yet created | After human creates the vault structure |
| 4.3 | Manual visual smoke-test | After human confirms login works |
| 6.1 | Server not provisioned | After platform-side `provision-server.yml` runs |
| 7.2 | Origin Cert manual update | After human adds the cert to webapp-management |
| 8.0 | Self-hosted-Runner opt-in is OPTIONAL & infra-dependent | Operator confirms this tenant should use the `netcup` pool (else skip → stays GitHub-hosted) |
| 8.3 | Security baseline failure | After issue investigated + fixed |
| 10.1 | Soak-period evidence review | After 7+ calendar days AND operator signs off on Tailscale-SSH-stability evidence |
| 10.2 | Public-SSH lockdown is destructive if Tailscale-SSH actually broke | Operator confirms break-glass path (Hetzner Rescue Console) is tested + reachable |

---

## 13. Troubleshooting

### Tailscale-SSH-Preflight failed

```
❌ Tailscale-SSH pre-flight failed.
   - tailscaled on target host not started with --ssh
```

Cause: Tailscale ACL doesn't permit `tag:ci-deploy → tag:server-<tenant>` as
deploy user.

Fix: Tailscale admin → ACL → Edit JSON → add to `ssh:` section.

### Cloudflared-Tunnel Connection Offline

Symptom: `curl https://<tenant>.bigler-consult.ch` → 502.

Diagnosis:
```bash
cd /c/Users/Micha\ Bigler/Documents/webapps/webapp-management
docker compose logs cloudflared --tail 50
```

Common causes:
- Stale `CF_TUNNEL_TOKEN` (rotated in Cloudflare-Zero-Trust)
- New hostname not added to tunnel ingress
- Origin Cert doesn't cover the hostname

### Deploy hangs at `docker image prune`

Symptom: workflow hangs ~5 min then errors out.

Cause: Parallel deploys on same server contending for daemon lock (S171-related).

Fix: workflow-templates v1.7.0+ has flock-protection. Verify caller uses
`@v2.0.0+`. If parallel deploys are expected, stagger them with
`concurrency: deploy-server-<server>` in the caller workflow.

### admin/ returns 200 without MFA

Symptom: After superuser-login, `/admin/` returns 200 immediately.

Cause: dcm version < 2.13.2, S19 admin-MFA middleware not active.

Fix: Bump `backend/requirements.txt` to dcm 2.13.3 or later. Re-deploy.

### Deploy succeeds but `/api/users/` returns 200 to anonymous

Symptom: Anonymous GET to API endpoints returns data.

Cause: dcm version < 2.12.0 — platform default still `[AllowAny]` (pre-S70-Phase-F).

Fix: Bump dcm to 2.13.3 or later. Re-deploy.

### `sync-secrets` says "Proton entry not found"

Cause: Proton-Pass vault structure doesn't match the schema in `secrets.yaml`.

Fix: Open Proton-Pass UI, navigate to the expected vault, manually create the
missing entries with the correct `source:` paths. Re-run `sync-secrets`.

---

## 14. References

| Doc | Purpose |
|---|---|
| `bigler-webapps/webapp-management/SECURITY_FINDINGS.md` | Central finding tracker (S1–S181+) |
| `bigler-webapps/webapp-management/ARCHITECTURE.md` | Platform topology, deploy flow, Tailscale, Cloudflare |
| `bigler-webapps/webapp-management-template/docs/ONBOARDING.md` | Platform-side onboarding (Steps 1–10 of infrastructure) |
| `bigler-webapps/webapp-management-template/docs/SECRETS.md` | Per-secret schema, rotation cadence |
| `bigler-webapps/webapp-management-template/docs/BREAK_GLASS.md` | Recovery runbook — especially Scenario 2 for re-opening tcp/22 after Section 10.2 lockdown |
| `bigler-webapps/webapp-management/RUNNER_HOST_BOOTSTRAP.md` | Self-hosted runner host bootstrap + the authoritative `runs-on: netcup` roll-out YAML (Section 8.0) |
| `bigler-webapps/django-core-micha/README.md` | Auth-library API |
| `bigler-webapps/workflow-templates/CHANGELOG.md` | Composite-action version history (for choosing pin) |
| `bigler-webapps/ui-core-micha/CHANGELOG.md` | UI-library version history |
| **This file** | App-side onboarding |

---

## 15. Acceptance Criteria — onboarding complete

The agent reports onboarding-complete only when ALL of these are true:

- [ ] Repo created from template; `develop` and `main` branches exist
- [ ] `project.yaml` configured with unique ports + correct domain
- [ ] `secrets.yaml` declared all required keys; all keys also exist in Proton
- [ ] GitHub Environments `staging` + `production` exist with all secrets populated
- [ ] Tailscale ACL includes `tag:server-<tenant>` for this tenant's server
- [ ] Cloudflare DNS + tunnel ingress configured
- [ ] First deploy on `develop` succeeded (staging container running)
- [ ] All 5 Section 8.3 security checks pass
- [ ] Kuma monitor registered + active
- [ ] `APP_FINDINGS.md` initialized for the tenant
- [ ] Central `webapp-management/SECURITY_FINDINGS.md` overview lists the new tenant

> **Note**: Section 10.2 (Public-SSH lockdown) is **NOT** part of onboarding-
> complete. It is a separate post-onboarding hardening step that runs AFTER
> a ≥ 7-day soak period (Section 10.1). The agent reports onboarding-complete
> with `tcp/22` still open from the public internet — this is intentional and
> documented as the break-glass path.

If any item is unchecked, the onboarding is NOT complete. Report the missing
items as a blocker-list to the human operator.
