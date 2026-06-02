# Tenant-Migration: Legacy → Current Platform State

**Zielgruppe:** AI-Agent (Claude oder Codex), der einen pre-modernization
Tenant (eigenes `webapp-management` + 1+ App, z.B. Photogallery) auf den
heutigen Stand des bigler-webapps Platform-Layers bringt.

**Referenz-Quelle für "current state":**
- Platform-Repo: `MichaBigler/webapp-management:main` (Persoenliches Platform-Repo des Operators)
- Template: `bigler-webapps/webapp-management-template:main` (= dieses Repo)
- App-Template: `bigler-webapps/webapp-template:main`
- Shared Renovate: `bigler-webapps/renovate-config:main`
- Libs: match current webapp-template pin (backend/requirements.txt at HEAD),
  match current webapp-template pin (frontend/package.json at HEAD)

**Annahme zum Ausgangszustand des Tenants:**
- `webapp-management` Klon noch ohne: shared-Renovate, update-server-Workflow,
  Kuma v2, dynamic middlewares (media-/upload-/auth-ratelimit), prConcurrentLimit-Tuning,
  concurrency-group in Deploy-Workflows, flock in deploy-app
- App (Photogallery o.ä.): Django <6, vite 7, vitest <4, pnpm <11, i18next 25,
  react-i18next 16, ggf. `reportWebVitals.js`, `gunicorn` als dead-dep,
  4 ungenutzte `@fontsource/*`-Fonts in `theme.js`, aeltere `ui-core-micha`

---

## Wichtige Vorab-Regeln fuer den Agent

1. **Tier-2-Sicht**: Praktisch alle Schritte sind Tier 2 (Major-Bumps, Auth-Code,
   Security-relevante Konfig). Vor jedem Phase-Commit: lokal testen → reviewer-Pass → User-Approval.
2. **Push-Branches**: App-Repo `develop`, Platform/Template `main`. Niemals `--force-push`.
3. **Daten-Migration ist einseitig** (Kuma v2, SafeFileField UUID-Rename) — Backup vor jedem
   solchen Schritt.
4. **Ein Phase = ein Commit**. Nicht mehrere Phasen mischen.
5. **UTF-8 ist Pflicht** ab Vite 8: vor jedem Commit
   `file frontend/src/**/*.{js,jsx,ts,tsx}` — alles muss `UTF-8 text` sein.
   Windows-1252-Files mit `iconv -f WINDOWS-1252 -t UTF-8` in-place konvertieren.
6. **Reviewer-Knowledge-Cutoff**: `actions/checkout@v6`, `pnpm@11`, `tiptap@3.23.5`,
   `Spring Boot 4`, `JDK 25` sind alle real und produktiv im bigler-webapps Workspace —
   falls ein Reviewer das als "doesn't exist" flaggt, ist das ein Knowledge-Cutoff-Fehler.

---

## Phase 0 — Discovery

```bash
# Im Tenant-Klon
git fetch --all
git status
git log --oneline -20

# Diff gegen die Templates
git diff bigler-webapps/webapp-management-template/main -- :^.github :^secrets.yaml
```

**Output erwartet:** Liste der Files die sich vom Template entfernt haben.
Diese Liste ist die Migrations-Arbeit.

**Schnell-Audit auf der App-Seite:**
```bash
# Backend
grep -E "^Django|gunicorn|pytest" <app>/backend/requirements.txt

# Frontend
node -e "const p=require('./<app>/frontend/package.json'); \
  console.log({vite:p.devDependencies?.vite, vitest:p.devDependencies?.vitest, \
    pnpm:p.packageManager, i18next:p.dependencies?.i18next, \
    react_i18next:p.dependencies?.['react-i18next'], \
    ui_core:p.dependencies?.['@micha.bigler/ui-core-micha']})"

# UTF-8 Audit (Vite-8-Blocker)
find <app>/frontend/src -type f \( -name '*.jsx' -o -name '*.js' \) | \
  xargs file | grep -v "UTF-8 text\|ASCII text"
```

---

## Phase 1 — Library-Pins synchron ziehen

**Zuerst, weil App-Modernization auf die aktuellen Pins baut.**

Lies die aktuellen Versions-Pins aus `webapp-template` HEAD:
- Backend: `bigler-webapps/webapp-template/backend/requirements.txt` — `django-core-micha`-Zeile
- Frontend: `bigler-webapps/webapp-template/frontend/package.json` — `@micha.bigler/ui-core-micha`-Eintrag

Wende die gefundenen Pins auf die App an:
```bash
# Backend — ersetze <CURRENT_PIN> mit dem aus webapp-template HEAD gelesenen Wert
# z.B.: django-core-micha==<CURRENT_PIN>

# Frontend — ersetze <CURRENT_PIN> mit dem aus webapp-template HEAD gelesenen Wert
# z.B.: "@micha.bigler/ui-core-micha": "<CURRENT_PIN>"
```

> Niemals Versionsnummern hartkodieren — immer aus webapp-template HEAD lesen.

**Kritisch:** Neuere `ui-core-micha`-Versionen koennen `react-i18next` von `dependencies`
nach `peerDependencies` verschieben. Ohne diesen Bump duplizieren sich i18next-Contexts
zwischen App und Lib → Translations brechen sichtbar. Changelog beim Bumpen pruefen.

Commit: `chore(deps): bump core libs to current webapp-template pin`

---

## Phase 2 — Renovate-Onboarding (App + Tenant-webapp-management)

**App-Repo:**
```json
// <app>/renovate.json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "github>bigler-webapps/renovate-config",
    "github>bigler-webapps/renovate-config:auto-merge"
  ]
}
```

**Tenant-webapp-management (Platform-Repo, main-Branch):**
```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "github>bigler-webapps/renovate-config",
    "github>bigler-webapps/renovate-config:auto-merge"
  ],
  "baseBranches": ["main"]
}
```

> Platform-Repos brauchen `baseBranches: ["main"]` Override (shared default ist `develop`).

**Mend-Installation pruefen**: Tenant-Owner muss Mend GitHub App auf seinen
Account und/oder Org installiert haben, sonst laeuft Renovate nicht.

Commit: `chore(renovate): onboard via shared bigler-webapps preset`

---

## Phase 3 — App-Modernization (Photogallery o.ä.)

> **Warnung: Die aufgefuehrten Versionsnummern waren korrekt zum Zeitpunkt der Dokumentenerstellung.**
> **Lies die aktuellen Pins stets aus webapp-template HEAD — diese Tabelle dient nur als Referenz (moeglicherweise veraltet).**

**Backend `requirements.txt` (Referenz-Versionen — aus webapp-template HEAD verifizieren):**
```
Django>=5.0,<6.0      → Django==6.0.5
gunicorn>=22.0        → DROP wenn Dockerfile CMD=daphne (typisch)
pytest                → pytest>=9.0
pytest-django         → pytest-django>=4.12.0
```

**Backend `Dockerfile` (Referenz-Versionen — aus webapp-template HEAD verifizieren):**
- `FROM python:3.12-slim` → `FROM python:3.14-slim`
- `corepack prepare pnpm@latest` → `corepack prepare pnpm@11.1.3 --activate` (Pin!)
- `COPY frontend/package.json frontend/pnpm-lock.yaml ./` →
  `COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc ./`

**Frontend `package.json` — Major-Bumps (Referenz-Versionen — aus webapp-template HEAD verifizieren):**
- vite 7 → 8.0.13
- vitest <4 → 4.1.6
- jsdom <29 → 29.1.1
- eslint 8 → 10.4.0  (+ neu `@eslint/js: ^10.0.1`)
- eslint-plugin-react-hooks 4 → 7.1.1
- globals 15 → 17.6.0
- i18next 25 → ^26.2.0
- react-i18next 16 → ^17.0.8
- @testing-library/user-event 13 → ^14.6.1
- packageManager: `pnpm@10.x.x...` → `pnpm@11.1.3`

**Frontend `package.json` — Cleanups:**
- Entfernen: `web-vitals` (CRA-Leftover, `reportWebVitals()` ist Dead-Code)
- Entfernen: `eslintConfig`-Block (CRA, von Flat-Config `eslint.config.js` ersetzt)
- Entfernen: `pnpm.onlyBuiltDependencies` (umzieht in `pnpm-workspace.yaml`)
- Entfernen: ungenutzte `@fontsource/*`-Fonts — `theme.js` audit, nur tatsaechlich
  in `fontFamily` referenzierte Fonts behalten. Latin-Subset: `import '@fontsource/dm-sans/latin-400.css'` statt bare `import '@fontsource/dm-sans'` (spart ~90% Payload).

**Frontend NEU/RESTRUKTURIERT:**

`frontend/eslint.config.js` (Flat-Config, falls noch nicht vorhanden):
Uebernehmen vom `webapp-template/frontend/eslint.config.js`.

`frontend/pnpm-workspace.yaml` (pnpm v11 Policy):
```yaml
allowBuilds:
  '@swc/core': true
  'core-js': true
  'esbuild': true
minimumReleaseAgeExclude:
  - '@micha.bigler/ui-core-micha'
```

`frontend/vite.config.mts` test-Block ergaenzen falls fehlt:
```js
test: {
  environment: 'jsdom',
  globals: true,
  setupFiles: './src/setupTests.js',
  css: false,
}
```

`frontend/src/setupTests.js`: `import '@testing-library/jest-dom/vitest';`

**Dead-Code loeschen** (User-Approval-pflichtig per Tier 2 — vorab fragen):
- `frontend/src/reportWebVitals.js`
- `frontend/src/App.test.js` (alte Jest-Syntax) → ersetzen durch `App.test.jsx`
  mit Vitest-Sanity-Pattern aus `webapp-template`

**Tiptap (falls verwendet):**
- Bumpen aller `@tiptap/*` auf `^3.23.5`
- Imports von `default` auf `named` umstellen:
  `import Heading from '@tiptap/extension-heading'` →
  `import { Heading } from '@tiptap/extension-heading'`
- `setContent(content, false)` → `setContent(content, { emitUpdate: false })`
- `useEditor({ ..., shouldRerenderOnTransaction: true, ... })` ergaenzen
  (v3-default ist false → MenuBar mit `isActive()` wuerde stale anzeigen)
- `tiptap-extension-image-resize` pruefen — falls nicht in src/ importiert, raus

**Docker-Compose:**
- `docker-compose.local.yml`: `redis:7-alpine` → `redis:8-alpine`

**Workflow-Datei:**
- `.github/workflows/main.yml`: `actions/checkout@v4` → `@v6`

**Validierung bevor Commit:**
```bash
cd frontend
CI=true pnpm install
pnpm test       # vitest sanity
pnpm build      # PRODUKTIONS-Build — catcht UTF-8-Probleme

cd ../          # back to repo root
docker compose -f docker-compose.yml -f docker-compose.local.yml build backend
docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm \
  --entrypoint "" backend pytest --tb=short -q
```

Commit:
```
chore(modernize): django 6.0.5 + python 3.14, vite 8 / vitest 4 / pnpm 11 + i18next 26 + user-event 14; drop gunicorn + reportWebVitals + dead fonts; redis 8
```

---

## Phase 4 — Tenant-webapp-management Platform-Catchup

**4.1 Workflows angleichen** (Vergleich gegen
`bigler-webapps/webapp-management-template/.github/workflows/`):

Erwartete Workflows (alle uebernehmen falls fehlend):
- `apply-rulesets.yml` (Branch-Protection-Sync)
- `backup.yml` (taegliche Backups + Verify)
- `deploy-traefik.yml` (Deploy auf Push to main, Matrix ueber Inventory)
- `janitor.yml` (Docker prune etc.)
- `maintenance.yml` (apt, security-updates)
- `provision-server.yml` (Neue Server bootstrappen)
- `restore.yml` (Disaster-Recovery)
- `sync-kuma-notifications.yml` (Discord-Webhooks → Kuma)
- `sync-ssh-access.yml` (Public-Keys-Verteilung) **(DEPRECATED — fallback only)**
- `update-server.yml` (Inkrementelle Server-Config-Pflege — inotify-Limits etc.)

**4.2 `dynamic/middlewares.yml` ergaenzen** (7 Middlewares aus dem
upload-hardening Bundle):
- `media-safe-headers`
- `media-inline-images`
- `media-inline`
- `body-limit-default` (2 MiB)
- `body-limit-upload` (10 MiB)
- `body-limit-large` (100 MiB)
- `auth-ratelimit` (60/min, burst 30)
- `upload-ratelimit`
Plus `forwardedHeaders.trustedIPs` Fix fuer client-IP-Resolution unter Cloudflare.

Source: vergleiche mit `MichaBigler/webapp-management/dynamic/middlewares.yml`.

**4.3 `docker-compose.yml`:**
- `image: louislam/uptime-kuma:1` → `:2` — **Migration ist einseitig**;
  vorab Backup-Workflow manuell triggern, Logs verfolgen
  (`docker logs -f uptime-kuma` kann Stunden dauern bei viel Heartbeat-Historie)
- `uptime-kuma.extra_hosts:` Public-Domains pro App auf demselben Server
  hinzufuegen (Hairpin-NAT-Workaround)

**4.4 Concurrency in Deploy-Workflows:**
Jeder App-Deploy-Workflow + `deploy-traefik.yml` braucht:
```yaml
concurrency:
  group: deploy-${{ ... computed environment ... }}
  cancel-in-progress: false
```
Plus server-side flock in der `deploy-app` Composite-Action (in workflow-templates).

**4.5 `prConcurrentLimit: 1` in shared config** ist bereits zentral gesetzt —
keine Aktion im Tenant noetig, greift automatisch ueber `extends`.

**4.6 `inventory/inventory.yaml`:**
Tenant traegt seine eigenen Server + Apps ein analog zu
`MichaBigler/webapp-management/inventory/inventory.yaml`. Schema:
```yaml
targets:
  <tenant-server>:
    github_environment: <env>
    deploy_user: deploy
    roles: [traefik, backup, maintenance, janitor, ssh_sync, restore]
    sync_staging_apps: [<app>]
    expected_container_tokens: [traefik, uptime-kuma, <app-token>]
```

**4.7 Renovate-Onboarding fuer den Tenant-Stack** (bereits in Phase 2 erledigt).

Commit-Set (pro Bereich einen Commit):
- `feat(workflows): adopt update-server / apply-rulesets / sync-* workflows`
- `feat(traefik): add media + body-limit + ratelimit middlewares`
- `chore(deps): bump uptime-kuma 1 → 2` (separat, eigener PR)
- `ci: add concurrency group to deploy workflows`

---

## Phase 5 — Security-Hardening (App)

**5.1 `/media/` Auth-Gate (siehe innoservice ee541c5 als Referenz)**

Wenn die App Files via Django serviert UND Traefik nicht `/media/*` an einen
authenticated-only Pfad routet → CRITICAL Leak.

Check:
```bash
grep -E "MEDIA_URL|serve\(|MediaServe" <app>/backend/<app>/urls.py
```

Fix-Pattern:
1. `urls.py` Deny-Route — nur `/media/public/*` zulassen, alles andere 404 in
   `not DEBUG`
2. Models: `upload_to=` auf UUID-Callable umstellen
   (`upload_to=lambda inst, fn: f"<scope>/{uuid4()}/{fn}"`)
3. Data-Migration: RunPython, idempotent + reversibel, alte Files umbenennen
4. ViewSet: `download` + `download_original` Actions die Permissions pruefen
5. Serializer: URL als `SerializerMethodField` (verhindert raw-Pfad-Leak)
6. Frontend: `mediaUrls.js` Helper, alle Komponenten nutzen authenticated
   Endpoint statt `MEDIA_URL`
7. Management-Command `reconcile_media_paths` als Post-Deploy-Safety-Net

**5.2 SafeFileField ueberall wo Uploads ankommen**

`django-core-micha` bringt `SafeFileField` + `SafeImageField` mit
magic-bytes-Validation + Filename-Sanitization. Pflicht-Kwargs:
`allowed_mimes=[...], max_size=N`.

Modelle umstellen:
```python
from django_core_micha.validators.upload import SafeFileField, SafeImageField

class Attachment(models.Model):
    file = SafeFileField(
        upload_to=upload_path_with_uuid,
        allowed_mimes=['application/pdf', 'image/jpeg', 'image/png'],
        max_size=10 * 1024 * 1024,
    )
```

Fuer jeden umgestellten Field:
- AlterField-Migration
- DRF-Test der HTTP-400 bei Magic-Bytes-Mismatch zurueckgibt
- Wrap im Serializer: `DjangoValidationError → DRFValidationError` (sonst 500
  statt 400 — bekannter Bug aus den Pilots reimbursements + survey_app)

**5.3 Permission-Audit aller ViewSets**

Suche nach `IsAuthenticated` als alleinige Permission auf ViewSets die
sensible Daten servieren — typischerweise nicht ausreichend, braucht
Org-/Project-/Tenant-Permission-Class.

---

## Phase 6 — CI/CD-Setup (App)

**6.1 `ci.yml`-Stub** (5 Zeilen, ruft Shared-Workflow auf):
```yaml
name: CI
on:
  pull_request:
    branches: [develop, main]
  workflow_dispatch:
jobs:
  ci:
    uses: bigler-webapps/workflow-templates/.github/workflows/app-ci.yml@main
    secrets: inherit
```

**6.2 `main.yml`** mit `concurrency:` (Pattern siehe webapp-template).

**6.3 `staging-health.yml`** Stub:
```yaml
name: Staging Health
on:
  pull_request:
    branches: [main]
  workflow_dispatch:
jobs:
  staging-health:
    uses: bigler-webapps/workflow-templates/.github/workflows/staging-health.yml@main
```

**6.4 `monitoring/monitor.yml`** mit `<app>-frontend` + `<app>-healthz`
Monitoren — `/api/healthz` muss live antworten (kommt aus `django-core-micha 2.9.0+`).

**6.5 Branch-Rulesets**: in `bigler-webapps/renovate-config/rulesets/repos.yml`
(oder Tenant-Pendant) den App-Slug eintragen mit `rulesets: [develop, main]`.

---

## Phase 7 — Server-seitiges Setup

(Erfolgt vom Tenant-Owner mit SSH-Zugriff, Agent koordiniert nur):

> **VERALTET: `sync-ssh-access.yml` ist deprecated.**
> **Kanonischer Weg: `ansible-provision.yml` mit `--tags ssh_sync`.**

> **Warnung: proton-pass-cli darf NICHT direkt aufgerufen werden.**
> **Alle Secrets werden ausschliesslich via `sync-secrets` Wrapper abgerufen.**

1. `sync-secrets --secret-source proton` lokal ausfuehren (CLI-Wrapper, gibt keine
   Secret-Werte aus) → Secrets in GitHub-Environments aktualisiert
2. SSH-Public-Keys aktualisieren via `ansible-provision.yml --tags ssh_sync`
3. Erster Deploy via Push auf `develop` (Staging) bzw. `main` (Production)
4. `register-kuma-monitors` (in Deploy-Workflow) registriert Monitore automatisch
5. Discord-Notifications via `sync-kuma-notifications.yml` zugewiesen

---

## Phase 8 — Validierung

Vor "fertig" deklarieren:

| Check | Ziel |
|---|---|
| `pnpm test` | gruen |
| `pnpm build` | gruen (production-build catcht UTF-8) |
| `pytest --create-db` | gruen, keine neuen Failures |
| `docker compose build backend` | exit 0 |
| Mend-Dashboard | "Detected Dependencies" zeigt manager-Liste, keine "Reason: undefined" |
| Renovate-Dashboard | "Repository problems" leer |
| Health-Endpoint live | `curl https://<domain>/api/healthz` → 200 |
| Kuma | `<app>-frontend` + `<app>-healthz` gruen |
| `/media/` Auth-Check | `curl https://<domain>/media/<sensitive>.pdf` → 403/404 ohne Auth |

---

## Bekannte Fallstricke (aus den Pilot-Sessions)

1. **Vite 8 / rolldown** ist strikt mit UTF-8. Windows-1252-Files brechen den
   build mit `stream did not contain valid UTF-8`. Pre-check verpflichtend.

2. **ui-core-micha duplicate**: wenn react-i18next sowohl in `dependencies` von
   ui-core-micha als auch in der App ist (Versions-Drift), gibt es **zwei
   i18next-Contexts** → Translations brechen. Loesung: neuere ui-core-micha-Versionen
   verschieben es nach `peerDependencies`.

3. **Tiptap v3** droppt default exports; alle Extension-Imports muessen
   `import { X } from '@tiptap/extension-x'` werden.
   `shouldRerenderOnTransaction: true` fuer MenuBar-State.

4. **pnpm v11** hat default `minimum-release-age` (~24h). Frisch publizierte
   Packages blocken den Install — `pnpm clean --lockfile && pnpm install` zur
   Re-Resolution oder pakete in `minimumReleaseAgeExclude` aufnehmen.

5. **Kuma v2 Migration ist EINSEITIG**, laeuft Stunden — Backup zwingend vor
   Image-Tag-Bump.

6. **Pillow** war frueher transitiv ueber `matplotlib`; nach matplotlib-Drop
   muss `Pillow` explizit in `requirements.txt`, sonst bricht `ImageField`-
   System-Check.

7. **gunicorn** ist in Docker-only-Deployments meist dead (CMD ist `daphne`).
   Grep alle Workflows + Dockerfile + docker-compose vor dem Drop.

8. **DjangoValidationError vs DRFValidationError**: DRF konvertiert Django-
   Validation-Fehler nicht automatisch zu HTTP 400 — explizit im Serializer
   wrappen, sonst HTTP 500 bei `SafeFileField`-Rejects.

9. **`actions/checkout@v6`** existiert real (v6.0.2, produktiv in 13 Repos im
   bigler-webapps Workspace). Reviewer mit Knowledge-Cutoff August 2025
   koennten das faelschlich als "not real" flaggen.

10. **`baseBranches: ["develop"]` im shared Renovate-Preset** ist
    App-Repo-Default. Platform/Lib-Repos brauchen `baseBranches: ["main"]`
    Override, sonst skip Renovate mit "Base branch does not exist".

---

## Rollback-Strategie

- **Phase 1 (Lib-Pins)**: trivial — frueheren Pin in requirements.txt /
  package.json, lockfile regenerieren.
- **Phase 2 (Renovate-Onboarding)**: trivial — renovate.json loeschen.
- **Phase 3 (App-Modernization)**: Branch zuruecksetzen, Docker-Image vom
  vorigen Tag rollen. Vor Phase 3 ein Snapshot des Production-Images ziehen.
- **Phase 4.3 (Kuma v1→v2)**: Image-Tag zurueck auf `:1`, `uptime-kuma-data`
  aus tarball restoren, `docker compose up -d uptime-kuma`.
- **Phase 5 (Security /media/-Gate)**: revert der urls.py-Aenderung +
  data-Migration `migrate <app> <prev_migration>`. **Achtung**: legacy-Files
  unter alten Pfaden sind eventuell schon umbenannt.

---

## Reihenfolge-Empfehlung

1. Phase 0 Discovery
2. Phase 1 Lib-Pins (kein Risiko)
3. Phase 2 Renovate-Onboarding (kein Risiko)
4. Phase 4.1-4.2 Workflows + Middlewares im Tenant-webapp-management
5. Phase 6 CI/CD im App-Repo (so dass Phase-3-Commits durch CI gehen)
6. Phase 3 App-Modernization (grosser Brocken, gut testen)
7. Phase 4.3 Kuma v2 (eigener Wartungs-Slot, Backup voraus)
8. Phase 4.4-4.6 Concurrency / Inventory
9. Phase 5 Security-Hardening (eigener Plan + planner_review)
10. Phase 7 Server-seitiges Setup
11. Phase 8 Validierung

Zwischen jeder Phase: Commit + Push + Mend-Dashboard kurz checken.

---

## Bei Unklarheit

Konsultiere als Referenz:
- `docs/new-app-checklist.md` (in MichaBigler/webapp-management, Persoenliches Platform-Repo des Operators) —
  wo App-spezifische Registries gepflegt werden
- `ARCHITECTURE.md` (in MichaBigler/webapp-management) — aktuelle
  Platform-Architektur
- Die Cloudflare-Tunnel-Architektur ist vollstaendig etabliert. Konfiguration via
  Terraform (`terraform/`) und `docs/ONBOARDING.md` Abschnitt A.7.
- `SECURITY_FINDINGS.md` (in MichaBigler/webapp-management) — komplette
  Findings-Liste mit Status
- `MEMORY.md` in `django-core-micha` — Release-Discipline, Pin-Migration-Story
