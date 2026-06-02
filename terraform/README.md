# Terraform Scaffold

Minimal Cloudflare + Terraform Cloud scaffold for a new tenant workspace.

## Quick start

1. Fill in `main.tf`: set your Terraform Cloud org and workspace name.
2. Fill in `variables.tf` defaults or supply values via `TF_VAR_*` env vars (see `docker-compose.yml`).
3. Add zone data sources and populate `cf_security_zones` in `cf-security-baseline.tf`.
4. Per server: copy `tunnel-example.tf` to `tunnel-<server>.tf` and replace placeholders.
5. Per domain: copy `origin-cert-example.tf` to `origin-cert-<domain>.tf` and replace placeholders.

See `docs/ONBOARDING.md A.7` for the full step-by-step walkthrough.

## File overview

| File | Purpose |
|---|---|
| `main.tf` | Provider + Terraform Cloud backend + shared locals |
| `variables.tf` | All input variables (tokens, IDs, domain) |
| `outputs.tf` | Tunnel IDs exported for reference |
| `cf-security-baseline.tf` | Bot Fight Mode + Leaked Credentials Detection for all zones |
| `tunnel-example.tf` | Tunnel + DNS + config pattern — copy per server |
| `origin-cert-example.tf` | Origin CA certificate pattern — copy per domain |
| `docker-compose.yml` | Local Terraform runner via Docker (reads `.env.local`) |

## Running Terraform locally

```sh
docker compose run --rm tf init
docker compose run --rm tf plan
docker compose run --rm tf apply
```

Secrets are read from `../.env.local` (never committed). See `docs/ONBOARDING.md A.7`.
