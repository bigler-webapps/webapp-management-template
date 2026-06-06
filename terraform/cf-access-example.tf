# Cloudflare Zero Trust Access — example scaffold
#
# Edge-Access-Gate fuer sensitive Hostnames. CF Access laeuft VOR dem
# Tunnel-Ingress: ein Browser-Request landet erst auf der CF-Access-
# Login-Seite; nach erfolgreichem Login wird der Request an
# cloudflared → Traefik → Origin durchgereicht.
#
# Empfohlene Eintraege zum Schutz:
# - status.<your-domain>              (Uptime Kuma)
# - traefik.<your-domain>             (Traefik Dashboard, prod)
# - staging-traefik.<your-domain>     (Traefik Dashboard, staging)
#
# Identity-Pfade (fuer alle Apps identisch):
# - GitHub OAuth (primary) → matched via Primary GitHub Email
# - One-time PIN (backup)  → matched via beliebige Mail (in Allowlist)
#
# Diese Resources nutzen den separaten provider-Alias cloudflare.access
# mit eigenem API-Token (siehe main.tf + variables.tf).
#
# Voraussetzung: Identity Providers (GitHub OAuth + OTP) muessen einmalig
# manuell im CF-Dashboard konfiguriert werden:
#   Zero Trust → Settings → Authentication → Login methods → Add
# Nach der Konfiguration deren UUIDs in die locals unten eintragen.

# --- Identity Providers (hard-coded UUIDs, nicht angelegt durch Terraform) ---
# IdPs werden manuell im CF-Dashboard konfiguriert. Wir referenzieren nur
# ihre UUIDs in der Policy-include als allowed_idps.
#
# In v5 wurde der name-basierte data-source-Lookup auf identity_provider_id-
# basierten Lookup umgestellt. Da die UUIDs stabil sind und sich nur bei
# Account-Migration aendern wuerden, hart-coden wir sie.
# UUIDs abrufen: CF Dashboard → Zero Trust → Settings → Authentication →
# Login methods → klick auf IdP → URL enthaelt die UUID.
locals {
  # VERIFY: UUIDs gelten nur fuer denselben CF-Account. Bei Account-Migration
  # oder Neukonfiguration der IdPs muessen diese Werte aktualisiert werden.
  github_idp_id = "YOUR-GITHUB-IDP-UUID"  # GitHub OAuth IdP UUID aus CF Dashboard
  otp_idp_id    = "YOUR-OTP-IDP-UUID"     # One-time PIN IdP UUID aus CF Dashboard

  # Single source of truth fuer alle Access-Apps und -Policies. Hinzufuegen
  # eines neuen Eintrags hier reicht — application + policy entstehen via
  # for_each automatisch.
  #
  # session_duration = 8h: eine Arbeitssitzung; gestohlener Session-Token ist
  # nicht ueber Wochen gueltig. (CF Default waere 24h.)
  access_apps = {
    kuma = {
      name   = "Uptime Kuma"
      domain = "status.example.com"  # Replace with your Kuma domain
    }
    traefik_prod = {
      name   = "Traefik Dashboard (prod)"
      domain = "traefik.example.com"  # Replace with your Traefik dashboard domain
    }
    # Add more entries as needed, e.g. staging-traefik, grafana, etc.
    # staging_traefik = {
    #   name   = "Traefik Dashboard (staging)"
    #   domain = "staging-traefik.example.com"
    # }
  }

  # Allowlist fuer alle Owner-Policies. Identity kommt vom IdP:
  # - GitHub-Login → matched gegen Primary GitHub Email des Accounts
  # - OTP-Login    → matched gegen die eingetippte Mail-Adresse
  access_allowed_emails = [
    "you@example.com",  # Replace with your email address(es)
  ]
}

# --- Applications -------------------------------------------------------------
# self_hosted = Standard fuer Apps hinter CF Tunnel.
# auto_redirect_to_identity = false (nicht gesetzt): zwei IdPs → Picker zeigen
# statt automatischem Redirect zu GitHub (bessere UX bei zwei IdPs).

resource "cloudflare_zero_trust_access_application" "app" {
  for_each = local.access_apps

  provider         = cloudflare.access
  account_id       = var.cloudflare_account_id
  name             = each.value.name
  domain           = each.value.domain
  type             = "self_hosted"
  session_duration = "8h"
  allowed_idps = [
    local.github_idp_id,
    local.otp_idp_id,
  ]
  http_only_cookie_attribute = "false"

  # v5: Policies sind jetzt inline statt standalone-Resource. Application
  # owned die Policies direkt — keine application_id-Referenz mehr noetig.
  # Include-Liste matched gegen die Identity die vom IdP zurueckkommt.
  policies = [{
    name       = "Owner only"
    decision   = "allow"
    precedence = 1
    include    = [for e in local.access_allowed_emails : { email = { email = e } }]
  }]
}
