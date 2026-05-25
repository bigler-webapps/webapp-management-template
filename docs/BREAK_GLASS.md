# Break-Glass Runbook

When things go wrong, follow this playbook. Test these paths at least once
before you depend on them in production.

## Scenario 1: UFW or SSH locks you out

**Symptom:** Cannot SSH to the server from any IP.

**Recovery via Hetzner Rescue Console:**

1. Hetzner Cloud → server → **Console** (browser-based console)
2. Log in as root with the password from your Hetzner Cloud account
3. Investigate:
   ```bash
   ufw status
   ufw allow ssh
   # or: systemctl status sshd
   ```
4. Document what went wrong in your incident log

If the server is not booting:
- Hetzner Cloud → server → **Rescue** → activate rescue system
- Mount the disk, fix from rescue env

## Scenario 2: Tailscale/VPN is locked or down (if you adopted it)

**Symptom:** Tailscale-only SSH is gone, but you have a public-IP fallback.

> **Pre-condition note**: this scenario only applies to tenants that completed
> the public-SSH lockdown described in
> [webapp-template/TENANT_ONBOARDING_GUIDE.md](https://github.com/bigler-webapps/webapp-template/blob/main/TENANT_ONBOARDING_GUIDE.md)
> Section 10.2. If the tenant is still inside the 7-day soak window (Section
> 10.1), `tcp/22` is still open from the public internet — just SSH normally
> from a stable IP and skip to Tailscale-diagnosis (step 3 below). Do **NOT**
> close 22 again until the soak gates pass.

**Recovery:**

1. If you kept a public-IP allowlist (`tcp/22 from your-static-ip`), SSH directly
2. If you locked SSH to Tailscale-only (post-soak per Section 10.2):
   - Use Hetzner Rescue Console (Scenario 1)
   - From rescue: `ufw allow 22/tcp` to temporarily reopen
   - SSH in normally from any stable network
3. Investigate Tailscale: `systemctl status tailscaled`, `tailscale status`,
   `tailscale debug netcheck`, check Tailscale admin → ACL + Devices for
   token-expiry or ACL drift
4. Once Tailscale-SSH works again, **re-lock public SSH**:
   ```bash
   ssh deploy@<tenant-slug>-prod.tail990d7f.ts.net "sudo ufw delete allow 22/tcp && sudo ufw status verbose"
   ```
   Then verify from a non-Tailscale network that `tcp/22` is closed again.
   Do not skip this — re-lock is part of the recovery, not a follow-up task.

## Scenario 3: Cloudflare account locked or DNS broken

**Symptom:** App is unreachable, DNS does not resolve correctly.

**Recovery:**

1. Get into Cloudflare via account-recovery flow (email-based + your backup MFA)
2. If account is permanently lost: register a new account, re-add domain (may take
   24-48h for DNS to propagate)
3. **Interim fallback:** if you have a registrar-level NS-record, you can point
   the domain back to the registrar's DNS and create A-records there
4. **App-level fallback:** if you used Cloudflare Tunnel only, configure Traefik
   to listen on public 80/443 directly (open UFW), point DNS at server IP

## Scenario 4: Backup is corrupt or restic password lost

**This is the worst-case.** Restic password lost = restic repo unrecoverable.

**Preventive measures:**

- Store `RESTIC_PASSWORD` in at least 3 places: password manager, encrypted USB
  stick in a different physical location, and a sealed paper backup
- Test a restore quarterly (use the `restore.yml` workflow with `target_env=zip-file`)

**Recovery if password lost:**

- All B2-stored snapshots are unrecoverable
- Reconstruct from any local DB-dumps that might still exist
- Reconstruct from media uploads if those are user-recoverable
- Inform users of data loss timeline

## Scenario 5: GitHub Actions runner unable to reach server

**Symptom:** Deploy workflows fail at SSH step.

**Diagnose:**

1. Is the server up? (Hetzner Cloud → server status)
2. Is SSH listening? (Connect locally from your laptop, or via Rescue Console)
3. Is the GitHub Action SSH key correct? (Compare with `~/.ssh/authorized_keys`)
4. Is the GitHub-hosted runner's egress IP being blocked? (Unlikely, but
   check UFW rules)

## Scenario 6: All apps down, server unresponsive

**Recovery:**

1. Hetzner Rescue Console
2. Check disk space: `df -h`
3. Check memory: `free -h`
4. Check Docker: `docker ps -a`, `systemctl status docker`
5. If full disk: clean up `/var/log/`, prune Docker, expand storage
6. If OOM: investigate which container, restart it, plan a bigger server
7. If hardware: open Hetzner support ticket, may need migration to new server
   - Restic snapshots can be restored to a new server (see ONBOARDING.md +
     restore.yml workflow)

## Standard recovery from full disaster

If you need to recreate the entire tenant on a new server:

1. Provision a new Hetzner server (same OS, similar specs)
2. Update `SSH_HOST` GitHub secret to new IP
3. Run `provision-server.yml`
4. Run `deploy-traefik.yml`
5. For each app: run `restore.yml` with `target_env=production`, `snapshot_id=latest`
6. Run app deploy workflows
7. Update DNS A-records to new server IP

Estimated time end-to-end: 2-4 hours assuming backups are intact and you have
all secrets in your vault.

## Contact points

| Service | Account-recovery URL | MFA backup? |
|---|---|---|
| Hetzner Cloud | https://accounts.hetzner.com/ | yes/no — fill in |
| Cloudflare | https://www.cloudflare.com/login/ | yes/no |
| Backblaze B2 | https://secure.backblaze.com/user_signin.htm | yes/no |
| GitHub | https://github.com/login | yes/no |
| Your domain registrar | (provider URL) | yes/no |

Print this filled-in version and put it somewhere you can reach without your
laptop.

## Quarterly tests

Schedule a 1-hour drill every quarter:

- Q1: Hetzner Rescue Console — log in, navigate, log out
- Q2: restic restore test — `restore.yml` to `zip-file`, download artifact, verify
- Q3: Tailscale failure simulation (if adopted) — disable + re-enable, verify Break-Glass
- Q4: Full disaster recovery dry-run — provision new server, restore one app

Log the outcomes. If anything fails, fix it immediately — Break-Glass paths
that don't work are worse than not having them.
