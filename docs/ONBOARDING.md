# Onboarding Guide -- Infrastructure and App

> Agent-optimized. Stop at every PAUSE POINT for required human action or verification.
>
> **Scope:** This single document replaces the former `ONBOARDING.md` (infrastructure-side) and
> `TENANT_ONBOARDING_GUIDE.md` (app-side). Part A covers server provisioning; Part B covers
> tenant app creation and first-deploy. Both are required for a full tenant setup.
>
> **Time budget:** 30--60 min hands-on for Part B if Part A is already done.
> 3--5 h total if starting from scratch (mostly server-bootstrap waiting).

---

## Prerequisites

### Platform prerequisites

| Check | Verification command | Expected |
|---|---|---|
| Platform `webapp-management` is deployed | `gh api repos/<owner>/webapp-management --jq .default_branch` | `main` |
| Tailscale tailnet exists + agent has access | `tailscale status --json \| jq -r .Self.HostName` | the host name, not error |
| Cloudflare zone exists for the domain | `gh secret list --env production -R <owner>/webapp-management \| grep CF_` | `CF_TUNNEL_TOKEN`, `CF_ZONE_ID`, etc. populated |
| Proton-Pass entries for the tenant exist | Check Proton-Pass UI for `Projekt <Tenant-Name>` vault | Vault present, key categories: Database / Django / API-Keys / Infrastructure-Access / Mail |

Pause if any check fails. The platform must be ready before app-onboarding.

### Authorization to act

| Permission | How to verify |
|---|---|
| GitHub: create repo from template | `gh api user --jq .login` returns your login + you have write on the org |
| GitHub: create Environments | Repo Admin role required |
| Proton-Pass: read access to the tenant vault | Open Proton Pass, navigate to vault |
| Tailscale: ACL-write on tailnet (to add `tag:server-<tenant>`) | `tailscale auth -h` shows admin commands |
| Cloudflare: API-Token with `Zone:Edit` for the tenant zone | `curl -H "Authorization: Bearer $CF_API_TOKEN" "https://api.cloudflare.com/client/v4/zones?name=<domain>" \| jq` returns zone |

Pause if any permission is missing. Ask the human-operator to grant before proceeding.

### Required local tools

```bash
which git gh pnpm python node uv docker docker-compose || echo "MISSING_TOOL"
```

Expected: all tools present.

> Note: `proton-pass-cli` MUST NOT be called directly. All secret access goes through
> `sync-secrets --secret-source proton [flags]`. The `sync-secrets` wrapper (in
> `django-core-micha`) reads from Proton Pass without writing secret values to terminal output.
> Direct `proton-pass read ...` commands are forbidden.

Additional items needed:

| Item | Notes |
|---|---|
| GitHub account or organization | Where repos live |
| VPS provider account (Hetzner or similar) | A fresh server with root SSH key access |
| Domain name | DNS-controllable via Cloudflare |
| Backblaze B2 account | For restic backups (free tier suffices to start) |
| Approx. 3--5 hours | Spread over 1--2 days is fine |

---

## Part A -- Infrastructure: Provision a New Server

### A.1 Create your tenant repository

1. On GitHub, navigate to `https://github.com/bigler-webapps/webapp-management-template`
2. Click **Use this template** → **Create a new repository**
3. Pick a name in your org, e.g. `webapps-infra`
4. Make it private (this repo will hold your inventory and access keys)

Clone it locally:

```bash
git clone git@github.com:<owner>/webapps-infra.git
cd webapps-infra
```

### A.2 Adapt the inventory

```bash
cp inventory/inventory.example.yaml inventory/inventory.yaml
```

Edit `inventory/inventory.yaml`:

```yaml
version: 1
targets:
  production:
    github_environment: production
    deploy_user: deploy
    roles:
      - traefik
      - backup
      - maintenance
      - janitor
      - ssh_sync
      - restore
    sync_staging_apps: []
    expected_container_tokens:
      - traefik
```

Commit just the rename if you want it tracked, or keep it local-only (it is already in `.gitignore`).

### A.3 Prepare server secrets (secrets.yaml server class)

`secrets.yaml` (committed) defines the **schema** -- which secret keys exist.
`secrets.values.yaml` (gitignored) holds the **real values** per target.

```bash
cp secrets.values.example.yaml secrets.values.yaml
```

Edit `secrets.values.yaml` with your real values:

```yaml
targets:
  production:
    SSH_HOST: "203.0.113.10"
    SSH_USER: "deploy"
    SSH_PRIVATE_KEY: |
      -----BEGIN OPENSSH PRIVATE KEY-----
      ...
      -----END OPENSSH PRIVATE KEY-----
    SSH_PRIVATE_KEY_ROOT: |
      -----BEGIN OPENSSH PRIVATE KEY-----
      ...
      -----END OPENSSH PRIVATE KEY-----
    RESTIC_REPO_B2: "s3:..."
    RESTIC_PASSWORD: "..."
    # etc.
```

**Important:** `secrets.values.yaml` is gitignored. Never commit real secrets.
Back this file up separately (encrypted USB stick, password manager, age, etc.).

The full secret reference (which keys are required, formats, rotation) is in [SECRETS.md](SECRETS.md).

### A.4 Sync secrets to GitHub Environment

1. In your tenant repo on GitHub: **Settings → Environments → New environment**
2. Name it `production` (matching your inventory target)
3. Leave it empty for now -- `sync-secrets` will populate it

Push secrets from `secrets.values.yaml` to GitHub:

```bash
sync-secrets \
  --server \
  --secret-source yaml \
  --values-file secrets.values.yaml \
  --secret-target production
```

What this does:
- Reads `secrets.yaml` to know which keys exist
- Reads `secrets.values.yaml` to get the values for `production`
- Pushes each value as a GitHub Environment Secret in the environment resolved from `inventory.yaml`

After it completes, verify in **Repo → Settings → Environments → production → Environment secrets**
that all keys are present.

### A.5 Provision the server (ansible-provision.yml)

You need a fresh server. Suggested config:

- Ubuntu 22.04 or 24.04 LTS
- Minimum 2 vCPU, 4 GB RAM (more for Java-heavy apps)
- A root SSH key configured during creation

Note the public IP -- you will need it in the DNS steps below.

The canonical provisioning path is `ansible-provision.yml`, which runs the idempotent
`ansible/site.yml` playbook. It covers BOTH fresh-host bootstrap AND incremental
updates -- running it again later applies any added role/var without re-doing finished steps.

1. Ensure `ansible/inventory/hosts.yml` lists your host(s) and a matching
   `ansible/host_vars/<host>.yml` exists with `ansible_host` + `ansible_user`
2. In GitHub: **Actions → Ansible Provision → Run workflow**
3. Set `target` = your host (matches inventory + GitHub Environment name)
4. Click **Run workflow**

The workflow takes ~3--5 minutes for a fresh host. Expected role activity:

- apt update + upgrade
- Docker installation
- Tailscale install + auth-key registration (`tailscale up --ssh`)
- cloudflared install + tunnel-token registration
- Deploy user creation
- UFW configuration (deny incoming, allow 22/80/443 -- see note below)
- fail2ban setup
- Directory structure under `/srv/`

> **About `22/tcp` staying open**: provisioning leaves public-internet SSH (`22/tcp`) open
> by design. This is the **break-glass path** while Tailscale-SSH is unproven on the new server.
> Do NOT close `22/tcp` until the per-tenant 7-day Tailscale-SSH soak period has passed --
> see Part B, Section B.8 (soak criteria) and B.8.2 (lockdown procedure). If you close it
> too early and Tailscale fails, recovery is via the VPS rescue console only -- slow and stressful.

The legacy `provision-server.yml` + `update-server.yml` workflows are DEPRECATED -- they remain
in the template only for repos that have not yet adopted the Ansible role-set.

> **Note on SSH access sync**: the canonical path for syncing SSH access keys is
> `ansible-provision.yml --tags ssh`. The `sync-ssh-access.yml` workflow is deprecated and
> should be used only as a fallback if the Ansible path is unavailable.

**If something fails:** SSH into the server with your root key and investigate. Do not re-run
blind; understand what happened.

**PAUSE POINT:** Verify the provisioning workflow completed green in GitHub Actions before continuing.

### A.6 Deploy Traefik infrastructure

1. Adapt `docker-compose.yml` to your needs:
   - Set `DOMAIN_TRAEFIK`, `DOMAIN_KUMA`, `TRAEFIK_DASHBOARD_AUTH` in secrets
   - **Important:** `TRAEFIK_DASHBOARD_AUTH` needs `$$` (double-dollar) escaping -- see
     [DASHBOARD.md](DASHBOARD.md) for the gotchas
   - Remove the WireGuard service if you do not use it (use Tailscale instead)
   - Do NOT set `ACME_EMAIL` -- the platform uses Cloudflare Origin Certificates, not ACME/Let's Encrypt
2. Trigger `Deploy Traefik Infrastructure` workflow
3. Point your DNS records -- see A.7 below for the correct record types per use case

### A.7 Configure Cloudflare Tunnel + DNS + Origin Certificate

**TLS: Cloudflare Origin Certificates ONLY -- not ACME / Let's Encrypt.**

The platform uses the Cloudflare Tunnel + Origin Certificate architecture exclusively.
Do not configure ACME, certbot, or Let's Encrypt on any server. Origin Certs are issued
by Cloudflare and trusted end-to-end within the Cloudflare proxy -- they do not go through
the public CA ecosystem.

#### A.7.1 DNS records

For **apps routed through the Cloudflare Tunnel** (staging subdomains, production app domains
behind the proxy):

```
Type:    CNAME
Name:    <subdomain>
Content: <tunnel-uuid>.cfargotunnel.com
Proxied: yes (orange cloud)
```

Replace `<tunnel-uuid>` with the UUID from `webapp-management/secrets.yaml` `CF_TUNNEL_ID`.

For **direct-access records** (e.g. the raw server IP for admin/break-glass, Kuma on Tailnet,
or any host that does NOT go through the tunnel):

```
Type:    A
Name:    <host>
Content: <server-public-ip>
Proxied: no (grey cloud) -- only for direct access
```

#### A.7.2 Origin Certificate creation

For each apex domain (or wildcard) that needs TLS:

1. In the Cloudflare dashboard: **SSL/TLS → Origin Server → Create Certificate**
2. Select the hostnames: typically `*.yourdomain.com` and `yourdomain.com`
3. Choose validity (15 years is standard for origin certs)
4. Copy the certificate and private key
5. Place them on the server:
   - Certificate: `./certs/<domain>.pem`
   - Private key: `./certs/<domain>.key`
   - Permissions: `chmod 600 ./certs/<domain>.key`
6. Reference the cert paths in Traefik's TLS configuration

**PAUSE POINT:** Manual step. The platform-operator creates the cert via the Cloudflare dashboard,
copies the PEM/key content, and places it on the server before Traefik can serve TLS.

For staging subdomains under your apex domain: the existing wildcard cert
`*.<your-apex-domain>` typically already covers new subdomains. Verify coverage before
creating a new cert.

#### A.7.3 Update tunnel ingress

In `webapp-management/cloudflared/config.yml` add an entry for each new hostname:

```yaml
ingress:
  - hostname: <subdomain>.<domain>
    service: https://traefik:443
    originRequest:
      noTLSVerify: false
      caPool: /etc/ssl/certs/origin-ca.crt
  # ... other entries ...
  - service: http_status:404
```

Then redeploy cloudflared:

```bash
cd <webapps-root>/webapp-management
docker compose up -d --force-recreate cloudflared
# OR via GitHub Actions:
gh workflow run deploy-traefik.yml --field target=staging
```

**VERIFY:** `curl -I https://<subdomain>.<domain>/` returns HTTP 200 or the expected
redirect/auth response -- NOT 502 or 503.

### A.8 Register Uptime Kuma monitors (Tailnet-Serve path :8443)

Kuma is accessed via Tailnet-Serve on port `:8443`. The monitoring infrastructure uses
Tailscale as the network path -- this is the current architecture, not a future upgrade.

```bash
cd <webapps-root>/webapp-management
gh workflow run sync-kuma-notifications.yml
```

Verify in the Kuma dashboard (`https://kuma.<your-domain>`) that monitors are active
and green.

### A.9 Verify backups

Set up Backblaze B2:

1. Create a B2 bucket
2. Create an Application Key with read/write permissions to that bucket
3. Note the `keyID`, `applicationKey`, and bucket URL
4. Add them to `secrets.values.yaml` under your target
5. Generate a strong `RESTIC_PASSWORD` (this is permanent -- losing it means losing
   access to all backups)
6. Push to GitHub:
   ```bash
   sync-secrets --server --secret-source yaml \
     --values-file secrets.values.yaml --secret-target production
   ```

Run the backup workflow manually once:

1. **Actions → Backup → Run workflow** → `production`
2. Check the logs -- restic should initialize the repo, snapshot, and exit cleanly

### A.10 Part A acceptance criteria

Before moving to Part B, verify:

- [ ] `ansible-provision.yml` completed green for the target host
- [ ] Tailscale installed and `tailscale status` shows the server as connected
- [ ] cloudflared tunnel running and connected (check Cloudflare Zero Trust dashboard)
- [ ] Traefik container running; dashboard reachable via Tailnet path
- [ ] Kuma container running; Kuma dashboard reachable
- [ ] Cloudflare Origin Certificate placed at `./certs/<domain>.pem` + `.key`
- [ ] DNS CNAME records pointing to tunnel UUID (for proxied domains)
- [ ] At least one successful restic backup snapshot verified in B2

---

## Part B -- App: Onboard a New Tenant App

### B.1 Create the tenant app repo from template

#### B.1.1 Decide naming

Settle the following before creating:

- **Tenant slug:** `<lowercase-no-spaces>` -- e.g. `acme-shop`. Used as project_name,
  domain prefix, DB-name, etc.
- **Display name:** human-readable, e.g. "ACME Shop".
- **Target domain:** e.g. `acme-shop.bigler-consult.ch` (staging) and `app.acme-shop.com` (production).
- **Owning org / GitHub:** typically `bigler-webapps` (org) or `MichaBigler` (personal).

**PAUSE POINT:** Confirm slug + domain with the human-operator before proceeding.

#### B.1.2 Create from template

```bash
gh repo create <owner>/<tenant-slug> \
  --template bigler-webapps/webapp-template \
  --private \
  --description "Tenant: <Display name>"
```

#### B.1.3 Clone + first orientation

```bash
git clone git@github.com:<owner>/<tenant-slug>.git
cd <tenant-slug>
git checkout -b develop main && git push -u origin develop
gh api repos/<owner>/<tenant-slug>/branches/main --jq .protection || echo "main not protected yet"
```

#### B.1.4 Configure branch protection (recommended)

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

Branch protection on `main` is non-blocking on the first-deploy flow but forces
release-promotion through PRs (develop → main).

**VERIFY:** `git branch --show-current` returns `develop`. Open the repo in GitHub to confirm
`main` and `develop` both exist.

### B.2 Configure project.yaml

#### B.2.1 Adapt the template

```bash
$EDITOR project.yaml
```

Replace placeholders. Reference: existing app `jg-ferien/project.yaml` is a good template.

Minimum config:

```yaml
project_name: <tenant-slug>
environments:
  staging:
    server: staging                # resolves via Tailscale to <tenant-env>.<your-tailnet-domain>
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
    host: kuma.<your-domain>
```

**VERIFY:** `python -c "import yaml; print(yaml.safe_load(open('project.yaml')))"` parses without error.

#### B.2.2 Pick unique ports

The agent MUST pick ports not yet used by other tenants. Check via:

```bash
for app in hram jg-ferien kerzenziehen innoservice survey_app survey_contact_app reimbursements; do
  grep -E "DB_HOST_PORT|WEB_PORT" "<webapps-root>/$app/secrets.yaml" 2>/dev/null | grep -oE '[0-9]+' | head -2
done
```

Pick `db_host_port` and `web_port` outside the union of the above.

### B.3 Configure secrets.yaml (app class)

#### B.3.1 Replace template placeholders

The template's `secrets.yaml` defines the SCHEMA. Each `source:` or `source_template:` URL
points into Proton-Pass. Update these to point to the new tenant's Proton vault.

Quick path: copy `jg-ferien/secrets.yaml` and find/replace:
- `proton://Projekt HRAM/` → `proton://Projekt <Tenant-Name>/`
- any other tenant-specific entry

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
- `SYNC_SHARED_SECRET` -- removed via S71 cleanup. Do not re-add.

**PAUSE POINT:** Verify all Proton-Pass entries exist before continuing. If missing, ask the
human-operator to create the vault structure.

#### B.3.2 Sync to local .env.local

```bash
sync-secrets --secret-source proton --secret-target local --values-file .env.local
```

This reads Proton-Pass and writes a local `.env.local`. The file is gitignored.

**VERIFY:** `.env.local` contains the declared keys; no value is empty (except deliberate `dev_default`s).

### B.4 Initial backend setup

#### B.4.1 Check current webapp-template pins at HEAD

Check the current pins in the template and verify the new tenant repo is using the latest
released versions of platform packages:

```bash
# Check current dcm pin in this repo
grep django-core-micha backend/requirements.txt

# Check latest released version
gh api repos/bigler-webapps/django-core-micha/releases/latest --jq .tag_name

# Check current ui-core pin in this repo
grep ui-core-micha frontend/package.json

# Check latest released version
pnpm view @micha.bigler/ui-core-micha version
```

If the repo is behind the latest release:
- Update `backend/requirements.txt`: `django-core-micha==<latest>`
- Update `frontend/package.json`: `"@micha.bigler/ui-core-micha": "<latest>"`
- Run `cd frontend && pnpm install --lockfile-only`

#### B.4.2 Verify backend/backend/urls.py matches S106-pattern

```bash
grep -E "accounts/.*allauth.urls" backend/backend/urls.py
# Expected: NO match -- accounts/ mount removed per S106
```

If a match exists, remove the line.

#### B.4.3 Run local migrations + test boot

```bash
cd backend
uv pip install -e .
python manage.py migrate
python manage.py createsuperuser     # interactive
python manage.py runserver 0.0.0.0:<web_port>
# Visit http://localhost:<web_port>/admin/ -- should redirect or show admin
```

Stop the server. Frontend test:

```bash
cd ../frontend
pnpm install
pnpm dev
# Open http://localhost:5173 -- should show login/landing
```

**PAUSE POINT:** Manual visual smoke-test that login flow works locally. The agent can skip if
browser-driven testing is not available.

### B.5 Configure GitHub Environments and push secrets

#### B.5.1 Create environments

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

#### B.5.2 Push secrets

```bash
sync-secrets --secret-source proton --secret-target staging
sync-secrets --secret-source proton --secret-target production
```

**VERIFY:** Each environment-secrets count matches `secrets.yaml` declared count.

```bash
gh api repos/<owner>/<tenant-slug>/environments/staging/secrets --jq '.total_count'
```

### B.6 Server-side provisioning

The agent MUST verify the target server is already provisioned by `ansible-provision.yml`
(see Part A) BEFORE proceeding.

#### B.6.1 Server pre-check

```bash
# Verify Tailscale ACL allows tag:ci-deploy → tag:server-<tenant>
tailscale status | grep <tenant-slug>
# If missing: add ACL entry in tailscale.com admin → ACL → Edit
```

If the server does not exist yet, **PAUSE** and execute Part A first.

#### B.6.2 Sync SSH access for the new tenant

The canonical path is `ansible-provision.yml --tags ssh`:

```bash
cd <webapps-root>/webapp-management
gh workflow run ansible-provision.yml --field target=staging --field tags=ssh
gh run watch
```

> Fallback only: if the Ansible path is unavailable, the deprecated `sync-ssh-access.yml`
> workflow can be used temporarily:
> `gh workflow run sync-ssh-access.yml --field target=staging`

#### B.6.3 Register the app's directory on the server

`webapp-management` provisions the apps' directory under `/srv/apps/<tenant>/` the first
time the deploy-app composite runs -- no manual server action needed.

### B.7 First deploy

#### B.7.0 (Optional) Self-hosted Runner-Pool -- opt-in

> **PAUSE POINT. This step is OPTIONAL and the default is to SKIP it.**
> By default a new tenant uses **GitHub-hosted** runners (`ubuntu-latest`). Only proceed if
> the operator has **explicitly decided** to move this tenant's CI/deploy onto the shared
> **self-hosted `netcup` pool** (cost reduction). If unsure, **skip to B.7.1** -- the tenant
> deploys fine on GitHub-hosted runners and can be switched later with zero data impact.

**Pre-condition (verify BEFORE switching):** a runner host must already be provisioned and `Idle`
in the org `runners` group, otherwise jobs pointed at `netcup` will hang `queued` forever.

```bash
# Expected: at least one self-hosted runner with label "netcup" in status "online".
# Requires org-admin scope (or a fine-grained PAT with the "Self-hosted runners" org permission).
# A repo-write-only onboarding token gets HTTP 403 here -- that is NOT "no runners".
# On 403, verify manually: org Settings → Actions → Runners.
gh api orgs/<owner>/actions/runners \
  --jq '.runners[] | select(.status=="online") | .name + " " + ([.labels[].name] | join(","))' \
  || echo "SCOPE_ERROR (likely 403, not admin) -- verify runners manually in org Settings → Actions → Runners"
```

**Pause if the pre-condition fails.** Provision a runner host first -- see
`bigler-webapps/webapp-management/RUNNER_HOST_BOOTSTRAP.md` -- then resume here.

Opt-in switches three workflows in the **tenant repo** to the pool
(authoritative YAML: `RUNNER_HOST_BOOTSTRAP.md §5`):

```yaml
# .github/workflows/ci.yml  (reusable App CI)
with:
  runs-on: netcup            # single label; default would be 'ubuntu-latest'
  run-backend: true
  run-frontend-tests: true

# .github/workflows/staging-health.yml
with:
  runs-on: netcup

# .github/workflows/main.yml  -- Deploy-Job
runs-on: [self-hosted, netcup]   # multi-label array (not the reusable input)
```

**VERIFY:** after pushing the workflow change, the next run executes on the pool.

```bash
# NOTE: use the REST jobs endpoint -- `gh run view --json jobs` does NOT expose
# runner_name, so it would always print "?" and give a false green.
RUN_ID=$(gh run list -R <owner>/<tenant-slug> --limit 1 --json databaseId --jq '.[0].databaseId')
gh api repos/<owner>/<tenant-slug>/actions/runs/${RUN_ID}/jobs \
  --jq '.jobs[] | .name + " → " + (.runner_name // "?")'
# Expected: runner_name is a netcup slot (e.g. <host>-slot1), NOT a GitHub-hosted runner.
```

#### B.7.1 Push develop to trigger Deploy App

```bash
cd <webapps-root>/<tenant-slug>
# Make a no-op commit to trigger CI
git commit --allow-empty -m "chore: trigger first deploy"
git push origin develop
gh run watch
```

#### B.7.2 Verify deployment

```bash
# Container running?
ssh deploy@<tenant-env>.<your-tailnet-domain> "docker ps --filter name=<tenant-slug> --format '{{.Names}} {{.Status}}'"

# Smoke-test HTTPS endpoint
curl -I https://<tenant-slug>.bigler-consult.ch/api/auth/_allauth/browser/v1/auth/session
# Expected: HTTP/2 200 (or 401 if no session) -- NOT 502 or 503
```

#### B.7.3 Security baseline checks

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
# Expected: 401 or 403 -- NOT 200

# /media/ media-safe-headers (if app has uploads)
curl -sI "https://$DOMAIN/media/some-file.pdf" 2>&1 | grep -iE "content-disposition|x-content-type-options"
# Expected: Content-Disposition: attachment; X-Content-Type-Options: nosniff
```

If any check fails, **PAUSE** and investigate. Do NOT proceed to production deploy.

### B.8 Post-staging soak gates

> Two independent gates fire after the staging burn-in window. Both require a **≥ 7-day soak
> period** with the staging deploy stable AND Tailscale-SSH proven as the primary access path.
> Do NOT combine these into one big-bang cutover -- production-promotion and SSH-lockdown are
> orthogonal risks.

#### B.8.1 Soak-period acceptance criteria (gate for B.8.2 and B.8.3)

The agent MUST verify ALL of the following before either lockdown or production-promotion
proceeds. The clock starts on the first successful staging deploy (Section B.7).
Earliest gate-pass: 7 calendar days later.

```bash
# Tailscale uptime >= 99% over the soak window (read Tailscale admin → Devices)
# Manual check -- record `last_seen` per device, compute downtime ratio.

# Zero Tailscale outages >= 5 minutes in the soak window
# Check via: tailscale status --json + tailscale debug netcheck history
# (or read Tailscale admin → Logs if Tailscale Business)

# At least 3 successful deploys via Tailscale-SSH path in the soak window
gh run list --workflow main.yml --branch develop --status success --limit 10 \
  --json conclusion,createdAt,headBranch \
  --jq '[.[] | select(.headBranch=="develop")] | length'
# Expected: >= 3

# Public-SSH-via-22 has NOT been used as fallback during soak
# Manual check -- operator's own action log; OR audit /var/log/auth.log on the server
ssh deploy@<tenant-env>.<your-tailnet-domain> "grep 'Accepted publickey' /var/log/auth.log | tail -20"
# Expected: source IPs are all 100.x.x.x (Tailscale CGNAT range) -- no public IPs
```

If ANY criterion fails, the soak resets. Restart the 7-day clock from the date the issue
was resolved.

**PAUSE POINT:** Manual human review of the soak evidence. The agent must NOT proceed to B.8.2
or B.8.3 without explicit operator approval recording the soak-pass.

#### B.8.2 Public-SSH lockdown (hardening -- after B.8.1 passes)

> **Why this is gated**: provisioning installs UFW with `tcp/22` open BY DESIGN -- that is the
> break-glass path while Tailscale is unproven. Closing it on day 1 would lock you out of the
> server the moment Tailscale has a hiccup (ACL drift, OAuth token expiry, tailscaled restart
> loop). The 7-day soak gives Tailscale a chance to fail loudly while you can still recover.

Once B.8.1 is signed off, lock down public-SSH:

```bash
# 1. Pre-flight: confirm Tailscale-SSH STILL works RIGHT NOW (do not trust soak alone)
ssh deploy@<tenant-env>.<your-tailnet-domain> "hostname && date"
# Expected: server hostname + current date -- NO password prompt

# 2. Pre-flight: confirm you can reach the VPS rescue console
#    (Provider Cloud → server → Console → log in once → log out)
#    This is the break-glass-of-the-break-glass.

# 3. On the server, deny tcp/22 from public:
ssh deploy@<tenant-env>.<your-tailnet-domain> "sudo ufw delete allow 22/tcp && sudo ufw status verbose"
# Expected status: 22/tcp not in the rules list; default policy `deny (incoming)`

# 4. Harden sshd to refuse password auth (defence-in-depth -- Tailscale-SSH uses SSO,
#    not passwords, but keep keys-only on the public bind too if it ever gets reopened
#    during break-glass):
ssh deploy@<tenant-env>.<your-tailnet-domain> "sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && sudo systemctl reload sshd"

# 5. VERIFY from a non-Tailscale network (e.g. phone hotspot):
ssh -o ConnectTimeout=5 deploy@<server-public-ip>
# Expected: timeout or "Connection refused" -- NOT a login prompt
```

**Reversal procedure**: see `webapp-management-template/docs/BREAK_GLASS.md` Scenario 2
for re-opening tcp/22 if Tailscale fails later.

#### B.8.3 Develop → Main promotion (production cutover -- after B.8.1 passes)

```bash
git checkout main
git pull --ff-only origin main
git merge --no-ff develop
git push origin main
# OR via PR:
gh pr create --base main --head develop --title "Release: develop → main"
```

Production deploy fires automatically on push to main.

**Repeat Section B.7.3 security checks** against the production domain.

### B.9 Initialize APP_FINDINGS.md

```bash
$EDITOR APP_FINDINGS.md
# Replace `<tenant-name>` with the actual tenant slug
# Update the `<APP>-NEW-` prefix convention to match tenant tag (e.g. ACME-NEW)
```

Add the tenant to the central `webapp-management/SECURITY_FINDINGS.md` overview tables as
a tracked repo.

### B.10 Register app monitoring

```bash
cd <webapps-root>/<tenant-slug>
# register-kuma-monitors composite reads project.yaml + creates Kuma monitors
gh workflow run main.yml --field environment=staging
# Or: monitor-only run via webapp-management
cd <webapps-root>/webapp-management
gh workflow run sync-kuma-notifications.yml
```

**VERIFY:** Open Kuma dashboard at `https://kuma.<your-domain>` and confirm a new monitor
for `<tenant-slug>` appears and is green.

---

## Decision-points and Pause-points summary

| Step | Pause-Reason | Resume-Trigger |
|---|---|---|
| Prerequisites | Platform-side infra not deployed | After Part A completes |
| Prerequisites | Authorization missing | After human grants permissions |
| A.7.2 | Origin Cert creation is a manual Cloudflare dashboard step | After human creates cert + places PEM/key on server |
| B.1.1 | Naming uncertain | User confirms slug + domain |
| B.3.1 | Proton-Pass entries not yet created | After human creates the vault structure |
| B.4.3 | Manual visual smoke-test | After human confirms login works locally |
| B.6.1 | Server not provisioned | After Part A `ansible-provision.yml` runs |
| B.7.0 | Self-hosted-Runner opt-in is OPTIONAL and infra-dependent | Operator confirms this tenant should use the `netcup` pool (else skip -- stays GitHub-hosted) |
| B.7.3 | Security baseline failure | After issue investigated + fixed |
| B.8.1 | Soak-period evidence review | After 7+ calendar days AND operator signs off on Tailscale-SSH-stability evidence |
| B.8.2 | Public-SSH lockdown is destructive if Tailscale-SSH actually broke | Operator confirms break-glass path (VPS rescue console) is tested + reachable |

---

## Troubleshooting

### Tailscale-SSH pre-flight failed

```
Tailscale-SSH pre-flight failed.
   - tailscaled on target host not started with --ssh
```

Cause: Tailscale ACL does not permit `tag:ci-deploy → tag:server-<tenant>` as deploy user.

Fix: Tailscale admin → ACL → Edit JSON → add to `ssh:` section.

### Cloudflared tunnel connection offline

Symptom: `curl https://<tenant>.bigler-consult.ch` returns 502.

Diagnosis:

```bash
cd <webapps-root>/webapp-management
docker compose logs cloudflared --tail 50
```

Common causes:
- Stale `CF_TUNNEL_TOKEN` (rotated in Cloudflare Zero Trust)
- New hostname not added to tunnel ingress (`cloudflared/config.yml`)
- Origin Cert does not cover the hostname

### Deploy hangs at `docker image prune`

Symptom: workflow hangs ~5 min then errors out.

Cause: Parallel deploys on same server contending for daemon lock (S171-related).

Fix: workflow-templates v1.7.0+ has flock-protection. Verify caller uses `@v2.0.0+`. If
parallel deploys are expected, stagger them with `concurrency: deploy-server-<server>`
in the caller workflow.

### admin/ returns 200 without MFA

Symptom: After superuser-login, `/admin/` returns 200 immediately.

Cause: dcm version < 2.13.2, S19 admin-MFA middleware not active.

Fix: Bump `backend/requirements.txt` to the latest dcm release. Re-deploy.

### Deploy succeeds but /api/users/ returns 200 to anonymous

Symptom: Anonymous GET to API endpoints returns data.

Cause: dcm version < 2.12.0 -- platform default still `[AllowAny]` (pre-S70-Phase-F).

Fix: Bump dcm to the latest release. Re-deploy.

### sync-secrets says "Proton entry not found"

Cause: Proton-Pass vault structure does not match the schema in `secrets.yaml`.

Fix: Open Proton-Pass UI, navigate to the expected vault, manually create the missing entries
with the correct `source:` paths. Re-run `sync-secrets`.

### DNS not propagating

Symptom: Traefik or cloudflared cannot resolve hostname; `dig` returns NXDOMAIN.

Fix: Wait 5--10 min after setting the CNAME/A record. Use `dig @1.1.1.1 <hostname>` to
query Cloudflare's resolver directly. Verify the record type (CNAME for tunneled, A for direct).

### SSH_PRIVATE_KEY_ROOT missing

Symptom: `ansible-provision.yml` fails to connect to the server.

Fix: Add the value to `secrets.values.yaml`, re-run `sync-secrets --server`, re-run the
provisioning workflow.

### Wrong server IP

Symptom: All workflows time out.

Fix: Update `SSH_HOST` in `secrets.values.yaml`, re-run `sync-secrets`, retry.

---

## References

| Doc | Purpose |
|---|---|
| `bigler-webapps/webapp-management/SECURITY_FINDINGS.md` | Central finding tracker (S1--S181+) |
| `bigler-webapps/webapp-management/ARCHITECTURE.md` | Platform topology, deploy flow, Tailscale, Cloudflare |
| `bigler-webapps/webapp-management-template/docs/SECRETS.md` | Per-secret schema, rotation cadence |
| `bigler-webapps/webapp-management-template/docs/BREAK_GLASS.md` | Recovery runbook -- especially Scenario 2 for re-opening tcp/22 after B.8.2 lockdown |
| `bigler-webapps/webapp-management/RUNNER_HOST_BOOTSTRAP.md` | Self-hosted runner host bootstrap + authoritative `runs-on: netcup` roll-out YAML (Section B.7.0) |
| `bigler-webapps/django-core-micha/README.md` | Auth-library API |
| `bigler-webapps/workflow-templates/CHANGELOG.md` | Composite-action version history |
| `bigler-webapps/ui-core-micha/CHANGELOG.md` | UI-library version history |
| `bigler-webapps/webapp-management/DASHBOARD.md` | Traefik dashboard auth + `$$` escaping gotchas |

---

## Acceptance Criteria -- Onboarding Complete

The agent reports onboarding-complete only when ALL of these are true:

**Part A (Infrastructure)**

- [ ] `ansible-provision.yml` completed green for the target host
- [ ] Tailscale installed; server appears in tailnet
- [ ] cloudflared tunnel running and connected (Cloudflare Zero Trust dashboard)
- [ ] Traefik container running; dashboard reachable
- [ ] Cloudflare Origin Certificate placed at `./certs/<domain>.pem` + `.key` (NOT ACME)
- [ ] DNS CNAME records pointing to `<tunnel-uuid>.cfargotunnel.com` for proxied domains
- [ ] At least one successful restic backup snapshot verified in B2
- [ ] Kuma dashboard live and monitoring the server

**Part B (App)**

- [ ] Repo created from template; `develop` and `main` branches exist
- [ ] `project.yaml` configured with unique ports + correct domain
- [ ] `secrets.yaml` declares all required keys; all keys also exist in Proton
- [ ] GitHub Environments `staging` + `production` exist with all secrets populated
- [ ] Tailscale ACL includes `tag:server-<tenant>` for this tenant's server
- [ ] Cloudflare DNS + tunnel ingress configured for the tenant's subdomain
- [ ] First deploy on `develop` succeeded (staging container running)
- [ ] All 5 Section B.7.3 security checks pass
- [ ] Kuma monitor registered + active for this tenant app
- [ ] `APP_FINDINGS.md` initialized for the tenant
- [ ] Central `webapp-management/SECURITY_FINDINGS.md` overview lists the new tenant

> **Note**: Section B.8.2 (Public-SSH lockdown) is NOT part of onboarding-complete. It is
> a separate post-onboarding hardening step that runs AFTER a >= 7-day soak period (B.8.1).
> The agent reports onboarding-complete with `tcp/22` still open from the public internet --
> this is intentional and documented as the break-glass path.

If any item is unchecked, the onboarding is NOT complete. Report the missing items as a
blocker-list to the human operator.
