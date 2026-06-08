# Server Migration Runbook

How to swap a server (replace staging/prod box, or add a node like `monitoring`)
**without** breaking public traffic, CI, or other tenants' tunnels.

> **Why this exists.** The 2026-06-01 staging→netcup + dedicated-monitoring move
> redeployed *compute* but skipped the *edge / DNS / secrets / state* layers — they
> surfaced one by one as outages: staging `522` (DNS still pointed at the dead box),
> an innoservice-prod incident (wrong tunnel token copied into another server's
> secret), a Kuma `404` (`DOMAIN_KUMA` set to the tailnet-serve URL instead of the
> public domain), and a fresh-Kuma crash-loop (no data migration + v2.4.0 wizard).
> Every item below maps to one of those failures. Work the list top-to-bottom and
> **do not power off the old server until the "Decommission" gate passes.**

---

## 0. Orient

- Repo: `webapp-management` (remote `bigler-webapps/webapp-management`). Infra repo →
  `main` + `develop` only. Terraform Cloud workspace `bigler-webapps/webapp-management`,
  **execution mode Local, merge to `main` = auto-apply.**
- Targets + their layers live in `inventory/inventory.yaml` (deploy-traefik),
  `ansible/inventory/hosts.yml` (provisioning), `terraform/*.tf` (DNS + tunnels),
  and Proton Pass `server-{target}/*` (per-server secrets).
- A server is **never** just compute. Every box has: compute, a public DNS
  record (A or tunnel-CNAME), usually its **own** CF tunnel, per-server secrets,
  tailnet membership + ACL grants, and possibly stateful volumes.

---

## 1. Provision the new box

1. New host in `ansible/inventory/hosts.yml` (correct group) + `host_vars/<host>.yml`
   (`ansible_host` = MagicDNS once joined; for first run override `-e ansible_host=<PUBLIC_IP>`).
2. `group_vars/<group>.yml`: `tailscale_advertise_tags`, UFW (tailnet-only SSH), retention, etc.
3. Proton `server-{target}/*`: `tailnet_auth_key` (pre-authorised for the right tag),
   `b2_*`/`restic_*` if backed up, `domain_traefik`, `domain_kuma` (if it serves Kuma),
   `tunnel_token` (see §4 — get this **after** the tunnel exists), `grafana_admin_password`, …
   → `sync-secrets --server --secret-target <target>`.
4. **Tailscale ACL** — add SSH grants for the new tag, for **both** identities used:
   - `tag:ci-provision → tag:server-<x>` as `provision` (ansible)
   - `tag:ci-deploy → tag:server-<x>` as `deploy` (deploy-traefik / deploy-app)
   > Gotcha: provisioning (as `provision`) succeeding does **not** mean deploy
   > (as `deploy`) will — they are separate ACL grants. Missing the `ci-deploy`
   > grant = `tailnet policy does not permit you to SSH to this node`.
5. `gh workflow run ansible-provision.yml -f target=<target>`.

---

## 2. Compute (apps + web stack)

- Apps deploy via Tailscale-SSH/MagicDNS — independent of public DNS, so they
  "work" even while public DNS still points at the old box. **This is the trap:
  green deploys ≠ public traffic served from the new box.**
- Web stack (traefik/cloudflared/kuma) deploys via `deploy-traefik` to hosts with
  `role: traefik` in `inventory/inventory.yaml`; `compose_profile` selects which
  `docker-compose.yml` services start (profiles are additive per host).
- `infra_container_tokens` must list exactly the containers that the chosen
  `compose_profile` starts (validation step). Don't list profile-gated services
  that won't run on this host.
- 🔴 **`IMAGE_NAME` collision on shared boxes.** `docker-compose.yml` uses
  `image: ${IMAGE_NAME:-ghcr.io/your-org/your-app-backend}`. If `IMAGE_NAME`
  isn't set in the rendered `.env` (e.g. a manual `docker compose up` instead of
  a CI deploy, or an app whose `project.yaml` `image_name` never reached the
  `.env`), the app falls back to that **placeholder tag** — and on a server that
  hosts several apps, they all collide on the same tag → one app silently runs
  another's image (seen: `bigler-consult_staging` ran `cockpit`'s build, which
  crashed on a setting only cockpit requires). Verify per box:
  `docker ps --format '{{.Names}}\t{{.Image}}'` → every app on its **own** distinct
  image, none on `your-org/your-app-backend`.

---

## 3. Edge / DNS — **the step everyone forgets**

Public traffic follows DNS, which is **Terraform-managed**, not the deploy.

- Direct-A hosts: `terraform/bigler-consult-ch.tf` → `locals.ip_<target>_v4`
  (+ the A-records referencing it). Repoint the IP to the new box.
- Tunnel-CNAME hosts: the `cloudflare_dns_record` CNAME → `<tunnel>.id.cfargotunnel.com`.
- Merge to `main` → auto-apply → ~1–2 min CF propagation.
> Gotcha: forgetting this = old hostnames `522` the instant the old box dies,
> even though the new box runs everything. `curl` every public hostname after apply.
> Gotcha: when **adding a brand-new** hostname, the self-hosted CI runner may have
> **negatively cached** the prior `NXDOMAIN` — the `staging-health` probe then fails
> `curl: (6) Could not resolve host` for up to the zone's SOA negative-TTL (~30 min)
> **even though it resolves fine externally**. Confirm with an external `curl`; don't
> chase a "real" outage. Re-run the probe after the negative TTL expires.

---

## 4. CF Tunnels + per-server token reconciliation — **highest-risk step**

- Each server has its **own** CF tunnel: `terraform/tunnel-<target>.tf`
  (`cloudflare_zero_trust_tunnel_cloudflared` + `_config` ingress + `config_src=local`).
  A new node needs a new `tunnel-<target>.tf` (mirror an existing one).
- **The tunnel token is NOT a Terraform output (v5).** After apply, get it from
  CF Dashboard → Zero Trust → **Networks → Connectors** → the tunnel → "Install /
  Connect" → the `eyJ…` string in `cloudflared service install <TOKEN>` (you do
  **not** install on Windows; that `eyJ…` value **is** the `TUNNEL_TOKEN` for the
  Docker cloudflared). Put it in Proton `server-{target}/tunnel_token` → sync.
- 🔴 **NEVER copy another server's token.** A tunnel token binds cloudflared to a
  specific tunnel. Put the wrong one in `server-<x>/tunnel_token` and that host's
  cloudflared joins the **other** server's tunnel as an extra connector → CF
  load-balances that *other* hostname across both → flapping `404/200` on a
  **different, unrelated** site (this is exactly how innoservice-prod broke).
  After deploy, confirm in `docker logs cloudflared` that the **ingress config**
  printed matches *this* host's hostnames.
- If you must stop the bleed: `docker stop cloudflared` on the offending host
  removes the rogue connector immediately.

---

## 5. Stateful services (Kuma, Loki/Grafana, DBs)

- **Decide data-migration vs fresh up front.** "No migration" = fresh setup wizard
  on first boot (and lost history).
- **Uptime-Kuma specifics:**
  - First boot → `/setup-database` wizard. If the half-initialised DB ever restarts
    before the wizard completes, v2.4.0 crash-loops on `no such table: setting`.
    Recovery: stop container, `rm` the data dir contents (`kuma.db*`, `db-config.json`,
    `mariadb/`), start, complete the wizard **in one sitting**.
  - The admin user you create **must** equal Proton `monitoring/kuma_automation_user`
    + `kuma_automation_password` — the CI (`register-kuma`) logs in via Socket.IO with
    those creds. **Kuma's built-in auth cannot be disabled** without breaking that CI
    (no API-key path for monitor CRUD).
  - **Two independent access paths — keep them straight:**
    - CI: `register-kuma` → `https://<host>.tailXXXX.ts.net:8443` via **Tailscale
      Serve** (bypasses Cloudflare). The serve mapping is set by an idempotent
      `tailscale serve --bg --https=8443 http://127.0.0.1:3001` task in
      `ansible/site.yml`. Needs a Tailscale HTTPS cert: `sudo tailscale cert <host>`
      (the ACME-DNS-01 `SetDNS 500` is usually transient — retry).
    - Humans: `status.bigler-consult.ch` via CF tunnel → traefik → kuma. This is the
      `DOMAIN_KUMA` Traefik `Host()` label.
  - 🔴 `DOMAIN_KUMA` (Proton `server-{target}/domain_kuma`) = the **public domain**
    (`status.bigler-consult.ch`), **not** the tailnet-serve URL. Wrong value →
    traefik has no matching router → public `404`. The serve URL belongs to the CI
    path (`KUMA_URL` / the register-kuma action default), a different thing.
  - The `register-kuma` action default URL lives in `workflow-templates`
    (`register-kuma-monitors` + `sync-kuma-notifications`); changing it = new tag +
    bump every app's `@vX.Y.Z`. CF Access fronts the public page; Kuma's own login
    is the second layer → expect a double login (structural, see runbook discussion).
- The `uptime-kuma-data` volume (incl. embedded-`mariadb/`) survives container
  restarts but **not** a box rebuild — back it up if continuity matters.

---

## 6. Verify BEFORE decommissioning the old box (gate)

- `curl -s -o /dev/null -w '%{http_code}' https://<each-public-hostname>/` for
  **every** hostname the old box served → all non-`52x`.
- `docker logs cloudflared` on the new box → ingress config lists this host's
  hostnames only; tunnel shows `HEALTHY` connectors.
- Cross-check no **other** site started flapping (rogue-connector check, §4).
- Only then power off / delete the old server.
- Update `BREAK_GLASS.md` (provider + IP + console access) and any
  `# <old-ip>`/provider comments in `terraform/*.tf`.

---

## 7. Repo / org transfer (a migration most people forget is one)

Transferring the repo (e.g. personal account → org) **wipes all GitHub
Actions secrets — repo-level AND every Environment.** The code keeps working,
but the **next deploy re-renders `.env` from now-empty secrets** and breaks
silently. Seen: `DOMAIN_KUMA` empty after transfer → Kuma router rule rendered
`Host(\`\`)` → public `404`.

After any transfer:
1. Re-point `config.target_repo` (+ runbook/docs) to the new owner.
2. **Re-sync repo-level secrets:** `sync-secrets --server` (no target) — repo-scoped
   secrets like `KUMA_SYNC_APP_*`.
3. **Re-sync EVERY environment, per target:** `sync-secrets --server --secret-target <target>`
   for **each** GitHub Environment (`main-prod`, `staging`, `monitoring`, …) — the
   per-server secrets (`DOMAIN_KUMA`, `CLOUDFLARE_TUNNEL_TOKEN`, `ORIGIN_*`, …) live
   there and are NOT covered by the repo-level sync.
4. Redeploy + verify (§6). Symptom of a missed env: a service that renders an
   empty value into `.env` (empty Traefik `Host()`, missing token, etc.).
> "It's in Proton" ≠ "it's in the GitHub Environment". `sync-secrets` only reads
> Proton and pushes outward; a transfer empties the GitHub side until you re-sync.

---

## Quick failure → cause map

| Symptom | Most likely cause | Fix |
|---|---|---|
| `522` on a hostname | DNS A-record / CNAME still points at old/dead box | §3 repoint + apply |
| Another site flaps `404/200` | wrong `tunnel_token` in a `server-*` secret (rogue connector) | §4 fix token + redeploy; `docker stop cloudflared` to stop bleed |
| CF `1033` | CNAME → tunnel with no running connector | start cloudflared with the **correct** token (§4) |
| Public `404` from traefik | `DOMAIN_KUMA`/`Host()` label ≠ requested host (or **empty** → `Host(\`\`)`) | §5 set `DOMAIN_KUMA` = public domain, redeploy |
| Env value empty in `.env` after a repo/org transfer | GitHub Environment secrets wiped by transfer, not re-synced | §7 `sync-secrets --server --secret-target <env>` per target |
| App runs the **wrong** code on a shared box | `IMAGE_NAME` unset → placeholder-tag collision | §2 ensure `IMAGE_NAME` set (CI deploy, not manual `compose up`) |
| `staging-health` red, `curl: (6) Could not resolve` (but resolves externally) | runner negatively cached the old `NXDOMAIN` for a just-added host | §3 wait out SOA negative-TTL (~30 min), re-run probe |
| Tailscale-SSH "policy does not permit" | missing ACL grant for `tag:ci-deploy`/`ci-provision` → server tag | §1.4 |
| `:8443` TLS handshake fails | missing Tailscale HTTPS cert | `sudo tailscale cert <host>` (retry on `SetDNS 500`) |
| Kuma crash-loop `no such table: setting` | half-initialised fresh DB | §5 wipe data dir + redo wizard once |
