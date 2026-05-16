# Traefik Web Dashboard — Setup & Troubleshooting

The default stack includes Traefik's web dashboard with BasicAuth. This doc covers the gotchas around bcrypt hashes in docker-compose-substitution chains.

## Quick recap of what you need

Three secrets feed the dashboard:

| Secret | Where | Example |
|---|---|---|
| `TRAEFIK_DASHBOARD_AUTH` | `secrets.values.yaml` → GitHub Env | `'admin:$$2y$$05$$BcryptHash...'` |
| `DOMAIN_TRAEFIK` | dito | `traefik.example.com` |
| `ACME_EMAIL` | dito | `you@example.com` |

Plus DNS: A-Record `traefik.example.com` → Server-IP.

## Generating the hash

```bash
# Easiest (Docker required):
docker run --rm httpd:2.4-alpine htpasswd -nbB admin "YourPassword"
```

Output:
```
admin:$2y$05$abcDEF123...
```

## The $$-escape rule

`docker-compose` interprets `$` as variable-reference. When the value contains a literal `$` (every bcrypt hash does), you must escape it as `$$`:

**htpasswd output:**
```
admin:$2y$05$abcDEF123...
```

**What goes into `secrets.values.yaml`:**
```yaml
TRAEFIK_DASHBOARD_AUTH: 'admin:$$2y$$05$$abcDEF123...'
```

Every single `$` → double `$$`. A bcrypt hash typically has 3 `$` signs (before `2y`, before `05`, before the body), so you'll get 3× `$$` after escaping.

Use **single quotes** around the value in YAML so the parser doesn't interpret anything itself.

**SAVE THE FILE** (Ctrl+S in VS Code) before running `sync-secrets`. This is the #1 reason the dashboard breaks on first deploy — the editor showed `$$` on screen but the file on disk still had `$`.

## How the value flows through the pipeline

```
secrets.values.yaml   →   GitHub Secret   →   .env on server   →   docker-compose substitution
admin:$$2y$$05$$Hash      admin:$$2y$$05$$Hash    admin:$$2y$$05$$Hash    admin:$2y$05$Hash
                                                                          ↑ compose reduces $$ to $
```

Every stage keeps `$$` literal. Only docker-compose's variable-substitution does the final reduction. If you see `$2y` somewhere before the final substitution, you have a `$$` missing earlier.

## Troubleshooting

### "variable XXXX is not set, defaulting to a blank string"

**Symptom:** Deploy-Workflow logs:
```
time="..." level=warning msg="The \"abcDEF123\" variable is not set. Defaulting to a blank string."
```

That `abcDEF123` is part of the bcrypt hash. Docker compose is trying to interpolate it as `$abcDEF123`.

**Cause:** `$$` somewhere in the pipeline collapsed to `$`. Find where.

**Diagnose on the server:**

```bash
ssh deploy@<server>
cd /srv/infrastructure/webapp-management
grep TRAEFIK_DASHBOARD_AUTH .env
```

| `.env` shows | Diagnosis |
|---|---|
| `admin:$$2y$$05$$...` (double `$`) | The pipeline is fine — issue is in docker compose or compose version mismatch |
| `admin:$2y$05$...` (single `$`) | `$$` was reduced before reaching the server. Check `secrets.values.yaml` is **saved** and contains `$$`. Re-run `sync-secrets` after saving. |

### Dashboard returns 404

DNS-Propagation. `dig +short traefik.example.com` must return the server IP. Wait 5-10 min, retry.

### BasicAuth prompt appears but password rejected

Wrong hash. Bcrypt is non-deterministic — generating the hash twice for the same password yields different hashes (both valid). Make sure the hash you put in `secrets.values.yaml` matches the password you're typing.

Generate a fresh hash, update `secrets.values.yaml`, save, `sync-secrets`, redeploy.

### Want to disable the dashboard

Remove from `docker-compose.yml`:
- `TRAEFIK_DASHBOARD_AUTH` env-var
- `--api.dashboard=true` command flag
- `traefik-auth` middleware label
- `traefik-dashboard` router labels

Redeploy. GitHub secrets can stay (unused).
