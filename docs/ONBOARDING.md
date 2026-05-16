# Onboarding — detailed walkthrough

This document walks you through setting up a new tenant from scratch.
Estimated time: 3-5 hours, mostly server-bootstrap waiting.

## Prerequisites

Before you start, have these ready:

| Item | Notes |
|---|---|
| GitHub account or organization | Where this template gets forked to |
| Hetzner Cloud Account (or other VPS provider) | A fresh server, root SSH key access |
| Domain name | DNS-controllable (Cloudflare, Namecheap, etc.) |
| Backblaze B2 account | For restic backups (free tier suffices to start) |
| Local tools | `git`, `gh` (GitHub CLI), `ssh-keygen` |
| Approx. 3-5 hours | Spread over 1-2 days is fine |

## Step 1 — Create your tenant repo

1. On GitHub, navigate to https://github.com/bigler-webapps/webapp-management-template
2. Click **Use this template** → **Create a new repository**
3. Pick a name in your org, e.g. `webapps-infra`
4. Make it private if you prefer (this repo will hold your inventory and access keys)

Clone it locally:

```bash
git clone git@github.com:YOUR-ORG/webapps-infra.git
cd webapps-infra
```

## Step 2 — Adapt the inventory

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

Commit just the rename if you want it tracked, or keep it local-only
(it's already in `.gitignore`).

## Step 3 — Bootstrap your server

You need a fresh Hetzner (or similar) server. Suggested config:

- Ubuntu 22.04 or 24.04 LTS
- Minimum 2 vCPU, 4 GB RAM (more for Java-heavy apps)
- A root SSH key configured during creation

Note the public IP — you'll need it in step 5.

## Step 4 — Prepare your secrets

`secrets.yaml` (committed) defines the **schema** — which secret keys exist.
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

## Step 5 — Create the GitHub Environment

1. In your tenant repo on GitHub: **Settings → Environments → New environment**
2. Name it `production` (matching your inventory target)
3. Leave it empty for now — `sync-secrets` will populate it

## Step 6 — Push secrets to GitHub

One command does the bulk-push from `secrets.values.yaml` to GitHub:

```bash
sync-secrets \
  --target github \
  --secret-source yaml \
  --values-file secrets.values.yaml \
  --secret-target production
```

What this does:
- Reads `secrets.yaml` to know which keys exist
- Reads `secrets.values.yaml` to get the values for `production`
- Pushes each value as a GitHub Environment Secret in the environment resolved
  from `inventory.yaml` (also `production` in our example)

After it completes, verify in **Repo → Settings → Environments → production →
Environment secrets** that all keys are present.

The full secret reference (which keys are required, formats, rotation) is in
[SECRETS.md](SECRETS.md).

## Step 7 — Provision your server

This installs Docker, fail2ban, UFW, creates the deploy user, and sets up
directories. It runs as root via `SSH_PRIVATE_KEY_ROOT`.

1. **Read the warning** at the top of `.github/workflows/provision-server.yml`
2. In GitHub: **Actions → Provision Server → Run workflow**
3. Select environment `production`
4. Click **Run workflow**

The workflow takes ~5 minutes. Watch the logs. Expected steps:

- apt update + upgrade
- Docker installation
- Deploy user creation
- UFW configuration (deny incoming, allow 22/80/443)
- fail2ban setup
- Directory structure under `/srv/`

**If something fails:** SSH into the server with your root key and investigate.
Don't re-run blind; understand what happened.

## Step 8 — Sync SSH access keys

After provisioning, sync the managed deploy keys from `access/` to your server.

1. Place your deploy public keys in `access/deploy/` (one file per key, `*.pub`)
2. Place any additional root keys in `access/root/`
3. Commit and push
4. Trigger `Sync SSH Access` workflow

The workflow will write your managed keys to `~/deploy/.ssh/authorized_keys`
and `/root/.ssh/authorized_keys` on the server.

## Step 9 — Deploy Traefik infrastructure

1. Adapt `docker-compose.yml` to your needs
   - Set your `DOMAIN_KUMA` in secrets (or hardcode if you prefer)
   - The Traefik web dashboard is **disabled by default** — see [DASHBOARD.md](DASHBOARD.md) if you want to enable it
   - Remove the WireGuard service if you don't use it (recommended — use Tailscale instead)
2. Trigger `Deploy Traefik Infrastructure` workflow
3. Point your DNS A-records to the server IP:
   - `traefik.yourdomain.com` → server IP
   - `kuma.yourdomain.com` → server IP (if you keep Uptime Kuma)
4. Wait for Let's Encrypt to issue certificates (1-3 minutes)
5. Visit `https://traefik.yourdomain.com` — you should see the BasicAuth prompt
6. Visit `https://kuma.yourdomain.com` — Uptime Kuma should be live

## Step 10 — Verify backups

Set up Backblaze B2:

1. Create a B2 bucket
2. Create an Application Key with read/write permissions to that bucket
3. Note the `keyID`, `applicationKey`, and bucket URL
4. Add them to `secrets.values.yaml` under your target
5. Generate a strong `RESTIC_PASSWORD` (this is permanent — losing it means
   losing access to all backups)
6. Push to GitHub:
   ```bash
   sync-secrets --target github --secret-source yaml \
     --values-file secrets.values.yaml --secret-target production
   ```

Run the backup workflow manually once:

1. **Actions → Backup → Run workflow** → `production`
2. Check the logs — restic should initialize the repo, snapshot, and exit cleanly

## What's next

- Deploy your first app (see your app repo's documentation)
- Set up monitoring (Uptime Kuma is included if you didn't remove it)
- Read [BREAK_GLASS.md](BREAK_GLASS.md) and test your recovery path
- Consider the upgrade path to Tailscale + Cloudflare Tunnel for hardened
  network access (see the platform's
  [ARCHITECTURE.md](https://github.com/bigler-webapps/webapp-management/blob/main/ARCHITECTURE.md))

## Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Forgot to set `SSH_PRIVATE_KEY_ROOT` in `secrets.values.yaml` | `provision-server` fails to connect | Add the value, re-run `sync-secrets`, re-run workflow |
| Wrong server IP in `SSH_HOST` | All workflows time out | Update the secret |
| Public keys not pushed | `sync-ssh-access` says "0 keys" | `git add access/ && git push` |
| Domain DNS not propagated | Traefik can't issue cert | Wait 5-10 min, check with `dig` |
| UFW closed off SSH | Cannot connect after provision | Use Hetzner Rescue Console |
