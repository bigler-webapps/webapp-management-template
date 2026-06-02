# Webapp Management Template

Startpunkt für eine neue Plattform-Instanz im bigler-webapps-Ökosystem.
Dieses Repo bringt einen Server, Traefik, Cloudflare-Tunnel, Monitoring und CI-Workflows —
alles was du brauchst, bevor die erste App deployed werden kann.

---

## Schnellstart für den Kollegen

> **Detaillierte Schritt-für-Schritt-Anleitung: [`docs/ONBOARDING.md`](docs/ONBOARDING.md)**
> Das Dokument ist agent-optimiert und führt vollständig durch Part A (Infrastruktur)
> und Part B (erste App). Lies es vor dem ersten Schritt einmal komplett.

### Was du brauchst, bevor du anfängst

| Voraussetzung | Details |
|---|---|
| **Proton Pass** | Zugang zur `webapp-management` Vault (enthält alle Plattform-Secrets). Wenn sie noch nicht existiert: `docs/ONBOARDING.md §A.0.1` erklärt den Aufbau. |
| **Tailscale** | Admin-Zugang zum Tailnet (für ACL-Regeln und Pre-Auth-Keys). |
| **Cloudflare** | Zone für deine Domain in Cloudflare. API-Token mit `Zone:Edit`. |
| **Backblaze B2** | Account für Restic-Backups (Free Tier reicht). |
| **GitHub** | Schreibzugang zur Org + Recht, Environments und Secrets anzulegen. |
| **VPS** | Frischer Server (Ubuntu 22.04/24.04 LTS, min. 2 vCPU / 4 GB RAM). |

### Drei Szenarien

**A) Neuer Server + neue App (von 0):**
→ `docs/ONBOARDING.md` Part A (Infrastruktur), dann Part B (App)

**B) Neue App auf bestehendem Server:**
→ `docs/ONBOARDING.md` Part B (App) — Part A überspringen

**C) Legacy-App migrieren:**
→ [`docs/MIGRATE_LEGACY_APP.md`](docs/MIGRATE_LEGACY_APP.md)

---

## Repo-Struktur

```
.github/workflows/        Thin-Wrapper-Workflows (rufen workflow-templates auf)
  ├─ ansible-provision.yml    Server provisionieren + aktualisieren (kanonisch)
  ├─ deploy-traefik.yml       Traefik-Stack deployen
  ├─ kuma-sync.yml            Uptime-Kuma-Monitore synchronisieren
  ├─ backup.yml               Tägliche restic-Backups
  ├─ sync-staging.yml         Staging-Sync (alle Apps)
  └─ restore.yml / restore-cross-server.yml
.github/scripts/          Python-Hilfsskripte für Inventory-Auflösung
access/
  ├─ deploy/                  Public Keys der Deploy-User
  ├─ infrastructure/          Public Keys der Infrastruktur-Admins
  └─ root/                    Public Keys für Root-Notfallzugang
ansible/                  Ansible-Rollen und Inventar
  ├─ site.yml                 Haupt-Playbook (idempotent, für CI und Erstlauf)
  ├─ inventory/hosts.yml      Zielserver (aus inventory.yaml generiert)
  └─ group_vars/              Vars pro Servergruppe (all, prod, stage, monitoring, runners)
terraform/                Cloudflare IaC (DNS, Tunnel, Origin-Certs, CF Access)
  ├─ main.tf / variables.tf / outputs.tf
  ├─ cf-security-baseline.tf
  ├─ tunnel-example.tf        → kopieren nach tunnel-<server>.tf pro Server
  └─ origin-cert-example.tf   → kopieren nach origin-cert-<domain>.tf pro Domain
docker-compose.yml        Traefik + cloudflared + Kuma (mit Profilen: main/staging/monitoring)
dynamic/                  Traefik Dynamic Config (Middlewares, Rate-Limits)
inventory/inventory.yaml  Inventory (gitignored; Beispiel: inventory.example.yaml)
secrets.yaml              Secret-Schema (committed; Werte kommen aus Proton Pass)
docs/
  ├─ ONBOARDING.md           Vollständige Anleitung (Part A + B)
  ├─ MIGRATE_LEGACY_APP.md   Legacy-Migration
  ├─ SECRETS.md              Secret-Schema-Referenz
  ├─ BREAK_GLASS.md          Notfall-Runbook
  └─ DASHBOARD.md            Traefik-Dashboard-Konfiguration
```

## Secrets-Architektur (Kurzversion)

Alle Secrets leben in **Proton Pass**, nie lokal auf Disk.

```
Proton Pass
├── webapp-management         ← Plattform-Vault (einmal pro Plattform)
│   ├── server-<target>/      ← pro Server: tailnet_auth_key, tunnel_token, B2-Keys, ...
│   ├── ci-tokens/            ← Tailscale OAuth (geteilt, alle Apps)
│   ├── cloudflare-api/       ← CF API-Token (geteilt)
│   ├── domain-<domain>/      ← Origin Cert + Key pro Domain
│   └── ...                   ← weitere geteilte Plattform-Secrets
│
└── <tenant-slug>             ← App-Vault (einmal pro Tenant-App)
    ├── django/secret_key
    ├── database/password
    └── mail/password
```

`sync-secrets --server --secret-source proton --secret-target <target>` liest aus Proton
und schreibt in GitHub Environment Secrets. Kein Zwischenschritt, kein lokales File.

## Weiterführendes

| Dok | Zweck |
|---|---|
| [`docs/ONBOARDING.md`](docs/ONBOARDING.md) | Vollständige Anleitung von 0 |
| [`docs/SECRETS.md`](docs/SECRETS.md) | Alle Secret-Keys, Formate, Rotation |
| [`docs/BREAK_GLASS.md`](docs/BREAK_GLASS.md) | Notfall-Recovery |
| [`docs/MIGRATE_LEGACY_APP.md`](docs/MIGRATE_LEGACY_APP.md) | Legacy-App migrieren |
| [`bigler-webapps/workflow-templates`](https://github.com/bigler-webapps/workflow-templates) | Shared Composite Actions |
| [`bigler-webapps/django-core-micha`](https://github.com/bigler-webapps/django-core-micha) | Shared Django Library |
