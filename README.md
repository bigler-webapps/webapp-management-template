# Webapp Management Template

A starting point for running your own server fleet with the bigler-webapps platform.

This template gives you everything you need to provision a Hetzner (or other)
server, deploy a Traefik reverse proxy, and orchestrate backups, maintenance,
and SSH access — using reusable composite actions from
[`bigler-webapps/workflow-templates`](https://github.com/bigler-webapps/workflow-templates).

## Who this is for

- You have your own GitHub account/org
- You have your own server (Hetzner, Scaleway, anywhere with SSH)
- You have your own domain
- You're comfortable with Docker, SSH, and a bit of GitHub Actions
- You want to deploy one or more Django + React apps without rebuilding the toolchain

If that matches, you're a "tenant" in this platform's casual-platform model.
The platform-side artifacts (workflow-templates, django-core-micha, ui-core-micha)
are public OSS that you consume. You operate your own infrastructure end-to-end.

## What this template gives you

```
.github/workflows/        # Thin wrappers that call the public composite actions:
  ├─ provision-server.yml     #   bootstrap a fresh server
  ├─ deploy-traefik.yml       #   deploy the Traefik infrastructure stack
  ├─ sync-ssh-access.yml      #   manage authorized_keys from access/
  ├─ backup.yml               #   daily restic backups (cron + manual)
  ├─ maintenance.yml          #   weekly OS updates (cron + manual)
  ├─ janitor.yml              #   monthly cleanup (cron + manual)
  └─ restore.yml              #   manual restore, production protected
access/                   # Managed public keys per role
inventory/                # Target inventory (example file)
infrastructure/traefik/   # Traefik + dynamic config
docs/                     # Detailed onboarding + reference
secrets.yaml              # Secret schema (committed)
secrets.values.example.yaml  # Example values per target (committed)
                          # → copy to secrets.values.yaml (gitignored) for real values
```

## Onboarding in 8 steps

See [docs/ONBOARDING.md](docs/ONBOARDING.md) for the detailed version with examples.

1. **Use this template** on GitHub → create a new repo in your org
2. **Clone locally** and copy `inventory/inventory.example.yaml` to `inventory/inventory.yaml`
   - Edit it to match your server(s)
3. **Adapt `secrets.yaml`** — add or remove keys as needed (the schema is committed)
4. **Copy `secrets.values.example.yaml` to `secrets.values.yaml`** — fill in real values
   (this file is gitignored)
5. **Have a Hetzner server ready** with SSH access via a root key
6. **Create a GitHub Environment** in your new repo with the same name as your target
   (e.g. `production`)
7. **Push secrets to GitHub** with one command:
   ```bash
   sync-secrets --target github --secret-source yaml \
     --values-file secrets.values.yaml --secret-target production
   ```
8. **Manually trigger `provision-server.yml`**, then `deploy-traefik.yml`

After that, you're ready to deploy your first app. Apps live in their own repos
and use a separate template ([`bigler-webapps/project-template-app`](https://github.com/bigler-webapps/project-template-app)
or your own structure).

## Important: workflow-templates version pinning

The wrappers in `.github/workflows/` currently reference
`bigler-webapps/workflow-templates/.github/actions/X@main`.

This is a TODO marked at the top of each workflow.

For production use, replace `@main` with a specific commit SHA or version tag.
This protects you from upstream surprises and satisfies supply-chain hygiene.

```yaml
# Example
uses: bigler-webapps/workflow-templates/.github/actions/deploy-traefik@<40-char-sha>  # v1.0.0
```

## Secrets philosophy

`sync-secrets` (from `django-core-micha`) reads:

- **`secrets.yaml`** — the schema (which keys exist), committed
- **`secrets.values.yaml`** — the values per target, gitignored

One command pushes values to GitHub Environment Secrets:

```bash
sync-secrets --target github --secret-source yaml \
  --values-file secrets.values.yaml --secret-target production
```

If you outgrow the local-values approach, you can later move secrets into Proton
Pass, Bitwarden, or 1Password — `secrets.yaml` then carries `source:` or
`source_template:` fields and `--secret-source proton`/`auto` does the rest.

See [docs/SECRETS.md](docs/SECRETS.md) for the full reference.

## What this template does NOT do

- Provision DNS, Cloudflare, or Tailscale — that's your tenant infrastructure
- Migrate live apps to a new host — that's your migration plan
- Backup your database with anything other than restic + B2
- Support apps with shared volumes across services without adaptation
- Replace your code-review process or testing strategy

If you need those, see the
[bigler-webapps architecture notes](https://github.com/bigler-webapps/webapp-management/blob/main/ARCHITECTURE.md).

## When things go wrong

See [docs/BREAK_GLASS.md](docs/BREAK_GLASS.md) for the disaster-recovery runbook.

If your SSH gets locked out, your last-resort path is the provider rescue console
(Hetzner Rescue System for example). Make sure you've tested it once before you rely on it.

## License

MIT. See [LICENSE](LICENSE).

## Updates

This template evolves. Bumping to a newer version is manual:

1. Open the [template repo](https://github.com/bigler-webapps/webapp-management-template)
   and look at the diff since you forked
2. Apply relevant changes to your repo
3. Test on staging before production

There is no automatic upgrade path — that's the price of casual-platform simplicity.
