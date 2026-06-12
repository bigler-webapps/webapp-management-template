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

### A.0 Prerequisites Setup — Prepare external services (Proton, Tailscale, Cloudflare, B2)

**Timing:** This section is entirely manual and external to the infra repo. Allocate **1--2 hours** here.
All steps must complete BEFORE A.1. The agent can skip already-done steps.

#### A.0.1 Proton Pass: Understand the two-vault architecture

The platform uses **two types of Proton Pass vaults**, each with a distinct purpose:

```
Proton Pass
├── webapp-management          ← PLATFORM vault (one shared vault for the whole platform)
│   ├── server-staging/        ← per-server items (one item group per server)
│   ├── server-main-prod/
│   ├── server-contact-prod/
│   ├── server-runners/
│   ├── server-monitoring/
│   ├── ci-tokens/             ← shared across ALL apps
│   ├── cloudflare-api/        ← shared
│   ├── github/                ← shared (runner GH App + Kuma sync App)
│   ├── terraform-cloud/       ← shared
│   ├── traefik/               ← shared
│   ├── monitoring/            ← shared (Kuma credentials)
│   ├── cross-server-restore/  ← shared
│   ├── shared-api-keys/       ← shared (MUI license, DeepL, etc.)
│   ├── domain-example.com/    ← per-domain (origin cert + key)
│   └── domain-other.com/
│
├── hram                       ← per-app vault (one vault per tenant app)
├── jg-ferien                  ← per-app vault
├── innoservice                ← per-app vault
└── <new-tenant-slug>          ← you will create this in A.0.2
```

**Proton path format used in `secrets.yaml`:**
```
proton://<vault-name>/<item-name>/<field-name>
```
Examples:
```
proton://webapp-management/ci-tokens/ts_oauth_client_id   ← shared
proton://webapp-management/server-staging/tailnet_auth_key ← per-server
proton://hram/django/secret_key                            ← per-app
```

**For a new server deployment, what you need to create:**
- In `webapp-management` vault: add a new `server-<target>` item group (A.0.2)
- In `webapp-management` vault: add a new `domain-<domain>` item group if new domain (A.0.3)
- Create a new per-app vault `<tenant-slug>` for the app's own secrets (A.0.4)

**What is already in `webapp-management` and does NOT need to be recreated:**

These items are platform-wide, shared by all tenants, and exist once per platform install:

| Item | Fields it must contain | Purpose |
|---|---|---|
| `ci-tokens` | `ts_oauth_client_id`, `ts_oauth_secret`, `ts_provision_oauth_client_id`, `ts_provision_oauth_secret`, `tailscale_mgmt_auth_key` | Tailscale OAuth for CI deploy + provisioning |
| `cloudflare-api` | `api_token`, `cf_access_token`, `account_id` | Terraform + CF Access |
| `github` | `github_runner_app_id`, `github_runner_installation_id`, `github_runner_private_key`, `github_runner_sha_256` | Self-hosted runner GH App + Kuma sync App |
| `terraform-cloud` | `api_token` | Terraform Cloud |
| `traefik` | `acme_email`, `dashboard_auth` | Traefik dashboard auth; `acme_email` is required by docker-compose even though TLS uses CF Origin Certs |
| `monitoring` | `kuma_url`, `kuma_automation_user`, `kuma_automation_password`, `discord_webhook_url` | Kuma automation + notifications |
| `cross-server-restore` | `transport_key` | Encrypted cross-server backup transport |
| `shared-api-keys` | `mui_license`, `deepl_api` | MUI X license (all apps), DeepL API |

**PAUSE POINT:** If the `webapp-management` vault does not exist yet (brand-new platform),
you must first set it up completely including all shared items before proceeding.
Ask the platform operator to confirm the vault exists and the shared items are populated.

---

#### A.0.2 Proton Pass: Add server-specific items to webapp-management vault

For each new server (`<target>` = inventory target name, e.g. `staging`, `main-prod`):

1. **Open Proton Pass → webapp-management vault**

2. **Create a new login/note item** named `server-<target>` (e.g. `server-staging`)

3. **Add these custom fields to the item:**

   | Field name | Value | How to obtain |
   |---|---|---|
   | `tailnet_auth_key` | Tailscale pre-auth key | A.0.5 below |
   | `tunnel_token` | Cloudflare Tunnel token | A.0.6 below |
   | `b2_key_id` | Backblaze B2 Application Key ID | A.0.7 below |
   | `b2_app_key` | Backblaze B2 Application Key secret | A.0.7 below |
   | `restic_repo` | `s3:s3.<region>.backblazeb2.com/<bucket>` | A.0.7 below — GitHub Secret key: `RESTIC_REPO_B2` |
   | `restic_password` | `openssl rand -base64 48` (run locally, save here) | Generate now |
   | `domain_traefik` | e.g. `traefik.<your-domain>` | Your DNS setup |
   | `domain_kuma` | e.g. `status.<your-domain>` (only on the monitoring server) | Your DNS setup |

   **Monitoring server only:** add a `server-monitoring` item to the **`webapp-management` vault** (same vault as all other server-* items — NOT a new vault) with an extra field:
   | Field | Value |
   |---|---|
   | `grafana_admin_password` | Strong random password for Grafana admin user |

   > **`restic_password` is permanent.** Generate it now with `openssl rand -base64 48`,
   > paste directly into Proton Pass, and do NOT save it anywhere else. Losing it = losing
   > access to all backups for this server.

4. **Repeat for each additional server** (staging, prod, etc.) — each gets its own item.

**PAUSE POINT:** All fields in `server-<target>` must be populated before A.0.3.

---

#### A.0.3 Proton Pass: Add domain-specific items to webapp-management vault (if new domain)

For each domain that needs Cloudflare Origin Certificate TLS (skip if the domain already exists):

1. **Open Proton Pass → webapp-management vault**

2. **Create a new item** named `domain-<domain>` (e.g. `domain-example.com`)

3. **Add these custom fields** — values will be filled in during A.0.6 (after you create the Origin Cert):

   | Field name | Value |
   |---|---|
   | `origin_cert` | PEM content of the Cloudflare Origin Certificate |
   | `origin_key` | PEM content of the private key |

   > Leave these empty for now. You will fill them in during A.0.6.

---

#### A.0.4 Proton Pass: Create per-app vault for the new tenant

Each tenant app gets its own Proton vault. The vault name is the tenant slug (lowercase, no prefix).

1. **Open Proton Pass → Create new vault**

2. **Name it exactly `<tenant-slug>`** (e.g. `acme-shop`) — lowercase, no spaces, no "Projekt" prefix

3. **Create these items inside the vault:**

   **Item: `django`**
   | Field | Value | Notes |
   |---|---|---|
   | `secret_key` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` | Generate fresh; never reuse |

   **Item: `database`**
   | Field | Value | Notes |
   |---|---|---|
   | `password` | strong random password (32+ chars) | `openssl rand -base64 24` |

   > Note: `DB_USER`, `DB_NAME`, `DB_HOST` are non-secret config values — they go in
   > `project.yaml` under `app_env`, not in `secrets.yaml`. See B.2.1.

   **Item: `mail`**
   | Field | Value |
   |---|---|
   | `password` | SMTP password or app-specific password for your mail provider |

   > `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER` are non-secret — they go in `project.yaml`.

   **Item: `social-auth`** (only if the app uses OAuth login)
   | Field | Value |
   |---|---|
   | `google_secret` | Google OAuth client secret |
   | `microsoft_secret` | Microsoft OAuth client secret |

   > The corresponding client IDs are non-secret and go in `project.yaml`.

   **App-specific items** (add as needed for the specific app's requirements)

4. **Do NOT add shared secrets here.** The following are already in `webapp-management` and
   referenced from there in `secrets.yaml` — do not duplicate:
   - `ci-tokens/*` (Tailscale OAuth, Kuma automation)
   - `shared-api-keys/*` (MUI license, DeepL)
   - `monitoring/*` (Kuma automation credentials)

**PAUSE POINT:** Per-app vault complete. Verify all items exist and fields are non-empty.

---

#### A.0.5 Tailscale: Create pre-auth key for the new server

This section requires Admin access to the tailnet.

1. **Open Tailscale admin console** (`https://login.tailscale.com/admin`)

2. **Verify the `tag:server` ACL and SSH rule already exists** (platform-wide, set up once):
   - Settings → ACL → check for an `ssh` block allowing `tag:ci-deploy` → `tag:server`:
     ```json
     "ssh": [
       {
         "action": "accept",
         "src": ["tag:ci-deploy"],
         "dst": ["tag:server"],
         "users": ["~user"]
       }
     ]
     ```
   - If missing, add it. This is a one-time platform setup, not per-server.

3. **Create a pre-auth key for the new server:**
   - Keys → Generate auth key → set:
     - **Ephemeral:** false (server stays persistent)
     - **Reusable:** false (one-time use)
     - **Expiration:** 24 hours (provision must complete within this window)
     - **Tags:** `tag:server` (or more specific tag if your ACL uses `tag:server-<role>`)
   - **Copy the key → immediately paste into Proton Pass:**
     - Vault: `webapp-management` → Item: `server-<target>` → Field: `tailnet_auth_key`
   - Do NOT save locally or paste elsewhere.

4. **The Tailscale OAuth clients** (TS_OAUTH_CLIENT_ID / TS_OAUTH_SECRET) for CI are
   platform-shared secrets that already exist in `webapp-management/ci-tokens/`.
   Do NOT create new OAuth clients per tenant.

**PAUSE POINT:** `tailnet_auth_key` field populated in Proton Pass `server-<target>` before A.0.6.

---

#### A.0.6 Cloudflare: Create tunnel, Origin Certificate, and DNS records

> **Platform API token and Account ID** are already in `webapp-management/cloudflare-api/`.
> Do NOT create new API tokens per tenant.

**Step 1: Create a new Cloudflare Tunnel for this server**

1. Cloudflare Zero Trust dashboard → Networks → Tunnels → Create tunnel
2. Choose **Cloudflared** as connector type
3. Name: `<target>` (e.g. `staging` or `main-prod`)
4. Copy the tunnel token that appears
5. **Immediately store in Proton Pass:**
   - Vault: `webapp-management` → Item: `server-<target>` → Field: `tunnel_token`
6. **Note the tunnel UUID** — visible in Zero Trust → Tunnels → click the tunnel name
   - You will need this for the DNS CNAME record below

**Step 2: Add the domain to Cloudflare (if new domain)**

If this domain is not yet in Cloudflare:

1. Cloudflare dashboard → Add a site → enter the domain (e.g. `example.com`)
2. Cloudflare shows two nameserver addresses (e.g. `ns1.cloudflare.com`, `ns2.cloudflare.com`)
3. At your registrar (Gandi, Namecheap, etc.): update the domain's nameservers to these two
4. Wait for propagation (usually 5 min, up to 48 h):
   ```bash
   dig NS example.com +short
   # Expected: Cloudflare nameserver addresses
   ```

**Step 3: Create Cloudflare Origin Certificate and store in Proton Pass**

TLS uses Cloudflare Origin Certificates — NOT ACME / Let's Encrypt.

1. Cloudflare dashboard → select domain → SSL/TLS → Origin Server → Create Certificate
2. Select hostnames: `*.example.com` and `example.com`
3. Choose validity: 15 years
4. Copy the **Certificate** (PEM) → **immediately store in Proton Pass:**
   - Vault: `webapp-management` → Item: `domain-<domain>` → Field: `origin_cert`
5. Copy the **Private Key** (PEM) → **immediately store in Proton Pass:**
   - Vault: `webapp-management` → Item: `domain-<domain>` → Field: `origin_key`
6. Do NOT save either value locally

> The `deploy-traefik` workflow reads these from GitHub Secrets (synced from Proton by
> `sync-secrets --server`) and writes them to `./certs/<domain>.pem` and `.key` on the server.

**Step 4: Create DNS records**

For **apps routed through the Cloudflare Tunnel** (most app subdomains):
```
Type:    CNAME
Name:    app              (for app.example.com)
Content: <tunnel-uuid>.cfargotunnel.com
Proxied: yes (orange cloud)
TTL:     Automatic
```

For **direct-access records** (staging server IP, Kuma on Tailnet, etc.):
```
Type:    A
Name:    staging          (for staging.example.com)
Content: <server-public-ip>  (the VPS IP from A.0.8)
Proxied: no (grey cloud)
TTL:     Automatic
```

**PAUSE POINT:** `tunnel_token` and `origin_cert`/`origin_key` in Proton Pass, DNS records created.

---

#### A.0.7 Backblaze B2: Create bucket + credentials per server

Each server gets its own B2 bucket. Free tier: 10 GB + 1 GB bandwidth/day.

1. **Sign up / log in** to Backblaze B2

2. **Create a bucket:**
   - Buckets → Create Bucket
   - Name: `<target>-backups` (e.g. `staging-backups`, `main-prod-backups`)
   - Lifecycle: enable "Keep prior versions for 30 days"
   - Save

3. **Create an Application Key restricted to this bucket:**
   - App Keys → Create Application Key
   - Name: `restic-<target>`
   - Capabilities: `listBuckets`, `readBuckets`, `writeBuckets`, `deleteFiles`
   - Bucket Restriction: select the bucket just created
   - **Copy `Application Key ID` → immediately store in Proton Pass:**
     - Vault: `webapp-management` → Item: `server-<target>` → Field: `b2_key_id`
   - **Copy `Application Key` secret → immediately store in Proton Pass:**
     - Vault: `webapp-management` → Item: `server-<target>` → Field: `b2_app_key`
   - Do NOT save locally

4. **Build and store the restic repo URL in Proton Pass:**
   - Format: `s3:s3.<region>.backblazeb2.com/<bucket-name>`
   - Find the region: Buckets → select bucket → Endpoint column
   - Example: `s3:s3.us-west-004.backblazeb2.com/staging-backups`
   - **Store in Proton Pass:** `server-<target>` → Field: `restic_repo`

5. **`restic_password` was generated in A.0.2** and already stored in Proton Pass.
   Verify it is non-empty there.

**PAUSE POINT:** All B2 fields populated in `server-<target>` in Proton Pass.

---

#### A.0.8 VPS Server: Procurement

Order a fresh server from your VPS provider (Hetzner, Linode, OVH, netcup, etc.). Minimum specs:

| Resource | Minimum | Recommended |
|---|---|---|
| vCPU | 2 | 4 |
| RAM | 4 GB | 8 GB |
| Storage | 50 GB SSD | 100+ GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| IPv4 | 1 public IP | 1 public IP |

> **No SSH keys needed.** Provisioning uses Tailscale-SSH keyless access via the `provision`
> user. The Tailscale auth key (stored in Proton in A.0.2) is sufficient for ansible-provision
> to reach the server after first-boot. Root password access at the VPS console is the
> break-glass path.

1. Choose Ubuntu 22.04 LTS or 24.04 LTS as the OS image
2. Set a strong root password at the VPS provider (used only for console break-glass)
3. After the server boots, **note the public IP address**
4. Verify the server is reachable (from the VPS console if needed, or via `ping <ip>`)

**Note the server's public IP** — you will need it for DNS A records in A.0.6.

---

**PAUSE POINT:** All of A.0 (Proton, Tailscale, Cloudflare, B2, VPS) must be complete and verified before proceeding to A.1.

---

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
  staging:
    github_environment: staging
    deploy_user: deploy
    compose_profile: staging          # maps to Docker Compose --profile flag
    roles:
      - traefik
      - restore              # staging is restore-destination only
      # backup/maintenance/janitor/ssh_sync are production-only roles
    sync_staging_apps: []
    infra_container_tokens:           # containers managed by deploy-traefik (not app containers)
      - traefik
      - cloudflared

  production:                # example production target
    github_environment: production
    deploy_user: deploy
    compose_profile: main
    roles:
      - traefik
      - backup
      - maintenance
      - janitor
      - ssh_sync
      - restore
    sync_staging_apps:
      - <tenant-slug>        # apps whose staging syncs from this server
    infra_container_tokens:
      - traefik
```

Commit just the rename if you want it tracked, or keep it local-only (it is already in `.gitignore`).

### A.3 Update secrets.yaml and sync to GitHub

`secrets.yaml` (committed) is the **schema**: it declares which GitHub Secret keys exist and maps them to Proton Pass paths. It follows the two-vault architecture from A.0.1.

1. **Open `secrets.yaml`** and update the path placeholders to match your actual setup:

   The file uses `{target}` as a placeholder for the inventory target name. Example paths it should contain (already in the template):
   ```yaml
   TAILSCALE_AUTH_KEY:
     source_template: "proton://webapp-management/server-{target}/tailnet_auth_key"
   CLOUDFLARE_TUNNEL_TOKEN:
     source_template: "proton://webapp-management/server-{target}/tunnel_token"
   B2_KEY_ID:
     source_template: "proton://webapp-management/server-{target}/b2_key_id"
   CLOUDFLARE_API_TOKEN:
     source: "proton://webapp-management/cloudflare-api/api_token"
   TS_OAUTH_CLIENT_ID:
     source: "proton://webapp-management/ci-tokens/ts_oauth_client_id"
   ORIGIN_CERT_<YOUR_DOMAIN>:
     source: "proton://webapp-management/domain-<your-domain>/origin_cert"
     exclude_from_env: true
   ORIGIN_KEY_<YOUR_DOMAIN>:
     source: "proton://webapp-management/domain-<your-domain>/origin_key"
     exclude_from_env: true
   ```

   **Paths that need adapting:**
   - Replace `domain-<your-domain>` entries to match your actual domain item name(s) in Proton
   - Ensure `config.target_repo` is set to `<your-org>/<your-infra-repo>`

2. **Verify Traefik dashboard auth is in Proton Pass:**
   ```bash
   # If missing, generate:
   sudo apt-get install apache2-utils
   htpasswd -nb admin <strong-password>
   # Output: admin:$apr1$XXXX$...
   # Store in: webapp-management vault → traefik item → dashboard_auth field
   ```

3. **Push server-class secrets to GitHub for each target:**
   ```bash
   # From within your webapp-management repo clone:
   sync-secrets --server --secret-source proton --secret-target <target>
   # Example:
   sync-secrets --server --secret-source proton --secret-target staging
   sync-secrets --server --secret-source proton --secret-target main-prod
   ```

   `sync-secrets` reads `secrets.yaml` + `inventory/inventory.yaml` to resolve which
   GitHub Environment to push to, then fetches each `source:` / `source_template:`
   value from Proton Pass and pushes it as a GitHub Environment Secret.

4. **Verify in GitHub:**
   - Repo → Settings → Environments → `<target>` → Environment secrets
   - All declared keys should appear (values are masked)

**Full reference** (secret-vs-config rule, per-secret inventory, rotation) is in [SECRETS_STRUCTURE.md](SECRETS_STRUCTURE.md).

### A.4 Create GitHub Environments

Create one GitHub Environment per inventory target before running `sync-secrets`:

```bash
# Create staging environment
gh api -X PUT repos/<owner>/<infra-repo>/environments/staging \
  --input - <<'JSON'
{"deployment_branch_policy": null}
JSON

# Create production environment (example)
gh api -X PUT repos/<owner>/<infra-repo>/environments/main-prod \
  --input - <<'JSON'
{"deployment_branch_policy": null}
JSON
```

> The canonical secret sync is covered in A.3: `sync-secrets --server --secret-source proton --secret-target <target>`.
> The GitHub Environment just needs to exist first so the API call has a target.

### A.5 Provision the server (ansible-provision.yml)

**First-run bootstrap (fresh server — OPERATOR step, NOT CI):**

The `ansible-provision.yml` CI workflow connects as the `provision` user via Tailscale-SSH. A fresh server has neither the `provision` user nor Tailscale installed yet, so the CI workflow cannot reach it on the first run. You must bootstrap manually from a local machine that can SSH into the server via its public IP:

```bash
# From WSL / Linux on your admin machine:
cd <webapps-root>/webapp-management/ansible

# Temporarily override the host to use the public IP (the host_vars file uses
# the Tailscale hostname which does not exist yet on a fresh server):
ansible-playbook site.yml \n  --inventory inventory/hosts.yml \n  --limit <target> \n  --extra-vars "ansible_host=<server-public-ip> ansible_user=root" \n  --ask-pass
```

This first run installs Tailscale (using the auth key from Proton/GitHub), creates the `provision` user, and enables Tailscale-SSH. After it completes:
- The server appears in the Tailscale admin console
- `ansible_host` in `host_vars/<target>.yml` should be the Tailscale MagicDNS hostname (already set in the template)
- Subsequent runs via the `ansible-provision.yml` CI workflow use Tailscale-SSH keyless as the `provision` user

**PAUSE POINT:** Confirm the first-run bootstrap completed and the server is visible in Tailscale admin before triggering the CI workflow.

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
- Deploy user + provision user creation
- UFW configuration (deny incoming, allow 22/80/443 -- see note below)
- fail2ban setup
- Promtail install (log shipper → Loki on monitoring host)
- Swap configuration (`swap` role)
- Directory structure under `/srv/` (`srv_dirs` role)

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

1. Verify that A.3 (`sync-secrets --server`) already pushed these required variables as GitHub Secrets:
   - `DOMAIN_TRAEFIK`, `DOMAIN_KUMA`, `ACME_EMAIL`, `TRAEFIK_DASHBOARD_AUTH`
   - Sources: `webapp-management/server-{target}/domain_traefik`, `domain_kuma` and `webapp-management/traefik/acme_email`, `dashboard_auth` in Proton
   - `ACME_EMAIL` must be non-empty (docker-compose references it even when TLS uses CF Origin Certs)
   - `TRAEFIK_DASHBOARD_AUTH` needs `$$` (double-dollar) in the compose file -- see [DASHBOARD.md](DASHBOARD.md)
2. Trigger the `Deploy Infrastructure` workflow (exact name in GitHub Actions tab)
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

Replace `<tunnel-uuid>` with the UUID noted during tunnel creation in A.0.6 Step 1. There is no `CF_TUNNEL_ID` in `secrets.yaml` -- the UUID comes from the Cloudflare Zero Trust dashboard.

For **direct-access records** (e.g. the raw server IP for admin/break-glass, Kuma on Tailnet,
or any host that does NOT go through the tunnel):

```
Type:    A
Name:    <host>
Content: <server-public-ip>
Proxied: no (grey cloud) -- only for direct access
```

#### A.7.2 Origin Certificate -- automated placement via deploy-traefik

The cert content was stored in Proton Pass in A.0.3 (`origin_cert` + `origin_key` fields
in `webapp-management/domain-<domain>`). `sync-secrets --server` (A.3) pushed them as
GitHub Secrets (`ORIGIN_CERT_<DOMAIN_SLUG>` + `ORIGIN_KEY_<DOMAIN_SLUG>`).

The `Deploy Infrastructure` workflow reads these secrets and writes the files to the server:
- `./certs/<domain-slug>.crt` (e.g. `./certs/example-com.crt`)
- `./certs/<domain-slug>.key` (e.g. `./certs/example-com.key`)

The slug format is: domain with dots and hyphens only (e.g. `example.com` → `example-com`).

**You do not manually place cert files on the server.** Triggering `Deploy Infrastructure`
(A.6 step 2) handles this automatically.

For staging subdomains under your apex domain: the existing wildcard cert `*.<your-apex-domain>`
typically already covers new subdomains. Verify coverage before creating a new cert.

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

#### A.7.4 CF Zero Trust Access — dashboard and admin-UI protection

CF Access is the **primary authentication gate** for all dashboard/admin-UI hostnames
(Traefik dashboard, Uptime Kuma). It intercepts requests at the Cloudflare edge — before
they reach the tunnel — and presents a login page backed by GitHub OAuth or one-time PIN.

`TRAEFIK_DASHBOARD_AUTH` (BasicAuth) is a **secondary defense-in-depth** layer. It catches
direct-to-origin requests (e.g. via Tailnet) that bypass CF Access. It is not the primary
user-facing gate.

**What you need to configure once per CF account (manual dashboard steps):**

1. **Create Identity Providers** in Zero Trust → Settings → Authentication → Login methods:
   - Add **GitHub OAuth**: enter your GitHub OAuth App Client ID + Secret
   - Add **One-time PIN**: no credentials needed, just enable it
   - After saving each IdP, click on it → the URL contains the IdP UUID — copy it

2. **Update `terraform/cf-access-example.tf`** with your IdP UUIDs and domain names:
   ```hcl
   github_idp_id = "61f5c3fc-..."   # from CF Dashboard
   otp_idp_id    = "8d3f0186-..."   # from CF Dashboard

   access_apps = {
     kuma       = { name = "Uptime Kuma", domain = "status.<your-domain>" }
     traefik_prod = { name = "Traefik Dashboard (prod)", domain = "traefik.<your-domain>" }
   }

   access_allowed_emails = ["you@example.com"]
   ```

3. **Ensure `CLOUDFLARE_ACCESS_API_TOKEN` is in Proton Pass** and synced to GitHub:
   - The token needs `Zero Trust: Access: Apps and Policies - Edit` + `Identity Providers: Read`
   - Store in: `webapp-management` vault → `cloudflare` item → `access-api-token` field
   - `secrets.yaml` already declares `CLOUDFLARE_ACCESS_API_TOKEN` with the correct path

4. **Add `cloudflare_access_api_token` to the Terraform Cloud workspace** before applying:
   - Terraform Cloud → workspace → Variables → Add variable
   - Key: `cloudflare_access_api_token`, Value: the token, Type: Terraform variable, Sensitive: yes
   - Without this step, `terraform apply` fails with a missing-variable error from TF Cloud.

5. **Apply Terraform** to create the Access applications and inline policies:
   ```bash
   cd <webapps-root>/webapp-management
   terraform apply -target='cloudflare_zero_trust_access_application.app'
   ```

> **Note**: CF Access is NOT automatically updated by `deploy-traefik.yml`. Adding a new
> dashboard hostname requires adding an entry to `access_apps` in `cf-access-example.tf`
> and re-running `terraform apply`.

**VERIFY:** Open `https://status.<your-domain>` in a private browser window. You should see
the CF Access login page (not the app directly, and not a BasicAuth prompt).

### A.8 Sync Kuma notification channels (Tailnet-Serve path :8443)

Kuma is accessed via Tailnet-Serve on port `:8443`. This step syncs notification channels
(Discord webhooks, etc.) to the Kuma instance -- it does NOT register per-app monitors.
App monitors are registered via `kuma-sync.yml` in B.10.

```bash
cd <webapps-root>/webapp-management
gh workflow run sync-kuma-notifications.yml --field target=<target>
# target = inventory target name (e.g. monitoring, staging)
```

Verify in the Kuma dashboard (`https://kuma.<your-domain>`) that notification channels
are configured (Settings → Notification).

### A.9 Verify backups

B2 credentials were stored in Proton Pass (A.0.7) and synced to GitHub via `sync-secrets --server` (A.3). There is nothing new to configure here.

Run a manual backup to verify the pipeline end-to-end:

1. **Actions → Backup → Run workflow** → select `target` = `<your-target>`
2. Check the logs -- restic should initialise the repo on first run (or snapshot on subsequent runs) and exit cleanly

If backup fails with "repository does not exist": first run **Actions → Restic Init → Run workflow** for the same target, then retry.

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
container_prefix: <short-prefix>           # e.g. "as" for acme-shop
image_name: "ghcr.io/<your-org>/<tenant-slug>-backend"

# Non-secret runtime config (goes here, NOT in secrets.yaml):
app_env:
  DB_NAME: <tenant-slug>_db
  DB_USER: <tenant-slug>
  DB_HOST: db
  EMAIL_HOST: smtp.example.com
  EMAIL_PORT: "587"
  EMAIL_USER: noreply@<your-domain>
  # Add GOOGLE_CLIENT_ID, MICROSOFT_CLIENT_ID etc. as needed

environments:
  staging:
    server: staging                # Tailscale node name; resolves via MagicDNS
    use_traefik: true
    domains:
      - "<tenant-slug>.<your-staging-domain>"
  production:
    server: main-prod              # or the production server's Tailscale node name
    use_traefik: true
    domains:
      - "<your-production-domain>"
  local:
    use_traefik: false
    domains:
      - "localhost"
    web_port: <unique-port>        # pick from 8100-8199, not used by other local apps
    db_port: <unique-port>         # pick from 5433-5499, not used by other local apps
    frontend_port: <unique-port>   # Vite dev server; pick from 5174-5299
    redis_port: <unique-port>      # Redis; pick from 6380-6499
```

> **Non-secret config belongs in `app_env`**, not in `secrets.yaml`. Values like
> `DB_NAME`, `DB_USER`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, OAuth client IDs
> are not secrets and do not belong in Proton Pass.

**VERIFY:** `python -c "import yaml; print(yaml.safe_load(open('project.yaml')))"` parses without error.

#### B.2.2 Pick unique ports

The agent MUST pick ports not yet used by other tenants. Check the local environments in each app's `project.yaml`:

```bash
for app in hram jg-ferien kerzenziehen innoservice survey_app survey_contact_app reimbursements; do
  echo "=== $app ==="
  grep -E "web_port|db_port|frontend_port|redis_port" "<webapps-root>/$app/project.yaml" 2>/dev/null
done
```

Pick all four ports (`web_port`, `db_port`, `frontend_port`, `redis_port`) outside the union of the above.

### B.3 Configure secrets.yaml (app class)

#### B.3.1 Replace template placeholders

The template's `secrets.yaml` defines the SCHEMA. Each `source:` or `source_template:` URL
points into Proton-Pass. Update these to point to the new tenant's Proton vault.

Copy the existing `webapp-template/secrets.yaml` and replace `<tenant-slug>` with the actual tenant slug.

**Critical secrets that MUST exist in the per-app Proton vault (`<tenant-slug>`):**

| Proton path | GitHub Secret key | Notes |
|---|---|---|
| `proton://<tenant-slug>/django/secret_key` | `DJANGO_SECRET_KEY` | Generate fresh with `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `proton://<tenant-slug>/database/password` | `DB_PASSWORD` | DB password; username/name/host go in `project.yaml app_env` |
| `proton://<tenant-slug>/mail/password` | `EMAIL_PASSWORD` | SMTP password; host/port/user go in `project.yaml app_env` |
| `proton://<tenant-slug>/social-auth/google_secret` | `GOOGLE_SECRET` | Only if app uses Google OAuth |
| `proton://<tenant-slug>/social-auth/microsoft_secret` | `MICROSOFT_SECRET` | Only if app uses Microsoft OAuth |

**Shared secrets already in `webapp-management` vault (do NOT recreate — reference as-is):**

| Proton path | GitHub Secret key | Notes |
|---|---|---|
| `proton://webapp-management/ci-tokens/ts_oauth_client_id` | `TS_OAUTH_CLIENT_ID` | Shared Tailscale OAuth, all apps |
| `proton://webapp-management/ci-tokens/ts_oauth_secret` | `TS_OAUTH_SECRET` | |
| `proton://webapp-management/monitoring/kuma_automation_user` | `KUMA_AUTOMATION_USER` | Shared Kuma automation user |
| `proton://webapp-management/monitoring/kuma_automation_password` | `KUMA_AUTOMATION_PASSWORD` | |
| `proton://webapp-management/shared-api-keys/mui_license` | `VITE_APP_MUI_LICENSE_KEY` | MUI X license, all apps |
| `proton://webapp-management/shared-api-keys/deepl_api` | `DEEPL_API_KEY` | DeepL translation API -- add only if the app uses DeepL |

**Critical secrets that should NOT be in `secrets.yaml`** (deprecated/removed):
- `SYNC_SHARED_SECRET` -- removed via S71. Do not re-add.
- `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `KUMA_SSH_*` -- removed; deploy uses Tailscale-SSH keyless. Do not re-add.

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
curl -I https://<tenant-slug>.<your-domain>/api/auth/_allauth/browser/v1/auth/session
# Expected: HTTP/2 200 (or 401 if no session) -- NOT 502 or 503
```

#### B.7.3 Security baseline checks

The agent MUST execute these post-deploy security verifications:

```bash
DOMAIN="<tenant-slug>.<your-domain>"

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

Symptom: `curl https://<tenant>.<your-domain>` returns 502.

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

### ansible-provision.yml fails to connect (fresh server)

Symptom: SSH connection refused or timeout on first run.

Cause: Fresh server -- `provision` user and Tailscale not yet installed.

Fix: Run the first-time bootstrap manually as described in A.5 (operator local run with
`--extra-vars ansible_host=<public-ip> ansible_user=root`). After the first run
completes, the CI workflow will connect via Tailscale-SSH as `provision`.

---

## References

| Doc | Purpose |
|---|---|
| `bigler-webapps/webapp-management/SECURITY_FINDINGS.md` | Central finding tracker (S1--S181+) |
| `bigler-webapps/webapp-management/ARCHITECTURE.md` | Platform topology, deploy flow, Tailscale, Cloudflare |
| `bigler-webapps/webapp-management-template/docs/SECRETS_STRUCTURE.md` | Secret-vs-config rule, per-secret inventory, rotation |
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
