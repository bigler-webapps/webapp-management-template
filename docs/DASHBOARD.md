# Activating the Traefik Web Dashboard (optional)

The default template ships **without** the Traefik web dashboard. Traefik itself runs as your reverse proxy; you just don't get a browser UI for routes/services/etc. Most operators don't need it — Traefik logs + `docker ps` cover daily ops.

If you want the dashboard, follow this guide.

## Why opt-in?

The dashboard requires HTTP BasicAuth (a bcrypt-hashed password). Routing the htpasswd hash through GitHub Secrets, env-substitution, and docker-compose interpolation is fragile (`$`-signs get mis-interpreted in multiple layers). Keeping it out of the default keeps every new tenant from stumbling over the same gotcha.

## What you need to add

### 1. Two new secrets

In `secrets.yaml`, uncomment / add:

```yaml
secrets:
  # ... existing ones ...

  DOMAIN_TRAEFIK:
    {}
  TRAEFIK_DASHBOARD_AUTH:
    # htpasswd-format string, e.g. 'admin:$$2y$$05$$BcryptHashHere'
    # Note the $$ — must be DOUBLE dollars in secrets.values.yaml so compose
    # later interpolates them as single $.
    {}
```

In `secrets.values.yaml`:

```yaml
targets:
  production:
    # ... existing ones ...
    DOMAIN_TRAEFIK: "traefik.example.com"
    TRAEFIK_DASHBOARD_AUTH: 'admin:$$2y$$05$$YourBcryptHashHere'
```

Generate the hash:

```bash
# Linux/macOS/Git Bash with sed:
docker run --rm httpd:2.4-alpine sh -c "htpasswd -nbB admin 'YourPassword' | sed -e 's/\$/\$\$/g'"

# Windows cmd (manual):
docker run --rm httpd:2.4-alpine htpasswd -nbB admin "YourPassword"
# then replace every $ with $$ in the output before pasting into secrets.values.yaml
```

Then push:

```bash
sync-secrets --target github --secret-source yaml \
  --values-file secrets.values.yaml --secret-target production
```

### 2. Compose-File anpassen

Edit `docker-compose.yml`:

**a)** In the `traefik` service, add an environment variable and enable the API:

```yaml
services:
  traefik:
    environment:
      - DOCKER_HOST=tcp://docker-socket-proxy-traefik:2375
      - TRAEFIK_DASHBOARD_AUTH=${TRAEFIK_DASHBOARD_AUTH}   # ADD THIS
    command:
      # ... existing flags ...
      - "--api.dashboard=true"                              # ADD THIS
    labels:
      - "traefik.enable=true"
      # ... existing security-header labels ...

      # ADD: BasicAuth middleware
      - "traefik.http.middlewares.traefik-auth.basicauth.users=${TRAEFIK_DASHBOARD_AUTH}"

      # ADD: Dashboard router
      - "traefik.http.routers.traefik-dashboard.rule=Host(`${DOMAIN_TRAEFIK}`) && (PathPrefix(`/api`) || PathPrefix(`/dashboard`))"
      - "traefik.http.routers.traefik-dashboard.entrypoints=websecure"
      - "traefik.http.routers.traefik-dashboard.tls.certresolver=myresolver"
      - "traefik.http.routers.traefik-dashboard.service=api@internal"
      - "traefik.http.routers.traefik-dashboard.middlewares=sec-headers,dashboard-ratelimit@file,traefik-auth"
```

**b)** Activate the `dashboard-ratelimit` middleware. Edit `dynamic/middlewares.yml`, uncomment:

```yaml
http:
  middlewares:
    dashboard-ratelimit:
      rateLimit:
        average: 5
        burst: 10
        period: 1m
```

### 3. DNS-Record für `traefik.example.com`

Cloudflare oder anderer DNS-Provider — A-Record auf Server-IP.

### 4. Deploy

```bash
# Optional: validate compose locally
docker compose --env-file .env config

# Trigger deploy-traefik via GitHub Actions
```

Browser → `https://traefik.example.com` → BasicAuth-Prompt.

## Troubleshooting

### Warning: "variable XXXX is not set, defaulting to blank string"

Heißt: das `$$`-Escape in `secrets.values.yaml` ist nicht doppelt. Bcrypt-Hashes haben typisch 3 `$`-Zeichen (vor `2y`, vor `05`, vor dem Body) — alle müssen verdoppelt sein.

Test: `cat .env | grep TRAEFIK_DASHBOARD_AUTH` auf dem Server muss zeigen:
```
TRAEFIK_DASHBOARD_AUTH=admin:$$2y$$05$$Hash...
```

Wenn nur einfache `$` da stehen: in `secrets.values.yaml` korrigieren, `sync-secrets` erneut.

### Dashboard zeigt 404

DNS-Propagation? `dig +short traefik.example.com` muss Server-IP zurückgeben.

### BasicAuth-Prompt aber Login schlägt fehl

Falscher Hash. Hash für **dasselbe** Passwort neu generieren (htpasswd ist nicht-deterministisch), `secrets.values.yaml` updaten, `sync-secrets`, deploy-traefik erneut.

## Deaktivieren

Reverse die Schritte 1-2. Oder: Dashboard-spezifische Labels + Command-Flag wieder rausnehmen, `TRAEFIK_DASHBOARD_AUTH`-Verwendung entfernen, deploy-traefik erneut. Die GitHub-Secrets können bleiben — werden ignoriert, wenn nicht in compose referenziert.
