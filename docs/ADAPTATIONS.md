# Adaptations — what you MUST customize after forking

This template is designed to be useful out-of-the-box, but a few items are
tenant-specific and must be adapted. Walk this checklist before the first
deploy.

## Required adaptations

### 1. `inventory/inventory.yaml`

- [ ] Copied from `inventory.example.yaml`
- [ ] Target name(s) match your GitHub Environment name(s)
- [ ] `expected_container_tokens` lists containers you actually run
- [ ] `sync_staging_apps` is set (empty list if no staging configured)

### 1b. `secrets.yaml` and `secrets.values.yaml`

- [ ] `secrets.yaml` `config.target_repo` is set to your fork (`your-org/your-repo`)
- [ ] `secrets.yaml` `config.default_target` matches your inventory target name
- [ ] `secrets.yaml` keys cover everything your apps + infra need
- [ ] `secrets.values.yaml` copied from `secrets.values.example.yaml`
- [ ] `secrets.values.yaml` has a section per target with all real values
- [ ] `secrets.values.yaml` is NOT committed (verify `git status`)
- [ ] Backup of `secrets.values.yaml` exists outside the repo (encrypted)

### 2. `infrastructure/traefik/docker-compose.yml`

- [ ] Reviewed: is the WireGuard container needed? (Recommend: remove and use Tailscale instead)
- [ ] Reviewed: is Uptime Kuma needed? (Recommend: keep, set up via UI after deploy)
- [ ] `DOMAIN_TRAEFIK`, `DOMAIN_KUMA` are set via environment variables you've added
- [ ] `traefik_cert_dumper` container — keep if your apps need exposed certs; remove otherwise
- [ ] Watchtower image is version-pinned (not `:latest`)

### 3. `infrastructure/traefik/dynamic/middlewares.yml`

- [ ] `vpn-only` middleware: source ranges match your real VPN CIDR
  - If you use Tailscale: `100.64.0.0/10` (Tailscale CGNAT range)
  - If you use WireGuard: your configured subnet (e.g. `10.10.0.0/24`)
  - Remove `vpn-only` entirely if you're not using a VPN

### 4. Workflow wrappers (`.github/workflows/*.yml`)

- [ ] `bigler-webapps/workflow-templates/.github/actions/*@main` replaced with
      a specific commit SHA or version tag
- [ ] `actions/checkout@<sha>` SHA-pin verified (currently set, but renew when
      checkout has a new release)
- [ ] `restore.yml` has reviewers configured for `production-destructive` environment

### 5. GitHub Environment Secrets

See [SECRETS.md](SECRETS.md) for the full list. At minimum:
- [ ] SSH access (4 secrets)
- [ ] Backup (4 secrets)
- [ ] Traefik (4 secrets)

### 6. DNS configuration

- [ ] A-records for `traefik.yourdomain`, `kuma.yourdomain` point to your server IP
- [ ] App subdomains point to your server IP (per app)
- [ ] (Optional) Cloudflare proxy disabled if you use Traefik for TLS
      (recommended start: DNS-only at Cloudflare)

### 7. GitHub Environments configured

- [ ] `production` environment created
- [ ] `production-destructive` environment created with Required Reviewers
      (for restore.yml production target)

## Recommended adaptations

These improve security and operations but are not strictly required to start.

### Pin third-party actions to specific SHAs

Currently the wrappers pin `actions/checkout` and reference upstream
composite actions by `@main`. Best practice:

```yaml
uses: bigler-webapps/workflow-templates/.github/actions/deploy-traefik@<40-char-sha>  # v1.0.0
```

Use Dependabot in your repo to auto-suggest updates.

### Enable secret scanning + push protection

GitHub Settings → Code security → enable both. Cheap defense against
accidentally committing secrets.

### Set up branch protection

`main` branch should require PR + at least one review. Enable
"Require status checks" once you have CI.

### Set up Dependabot

`.github/dependabot.yml` for:
- GitHub Actions (weekly)
- Docker (weekly)
- Pip (your app repos, not this one)

### Add CODEOWNERS

If you have multiple maintainers, define `.github/CODEOWNERS` so reviewers
get auto-assigned.

## Tenant-specific extensions

When you deploy your apps:

### Per app

- App lives in its own repo (use `project-template-app` as a template, or your own)
- App repo has its own `deploy-app.yml` wrapper workflow
- App repo has its own secrets schema and GitHub Environment Secrets

### Per environment

- Each environment (production, staging) has its own GitHub Environment
- Each has its own set of secrets
- Each can have different protection rules

## Decisions to make

| Question | Default | When to revisit |
|---|---|---|
| Use vault or local values file for secrets? | Local `secrets.values.yaml` | When team grows beyond 2 |
| Use Cloudflare Tunnel for ingress? | No (start with public Traefik + Let's Encrypt) | After 1-2 weeks stable |
| Use Tailscale for SSH access? | No (start with public SSH + UFW) | After 1-2 weeks stable |
| Move to Ansible for provisioning? | No (the GitHub Action works) | When you have 3+ servers |
| Adopt Komodo or Kamal? | No | After 6+ months of growing pains |

Document your decisions and rationale in a `DECISIONS.md` in your repo so
future-you (or your colleague) understands why.
