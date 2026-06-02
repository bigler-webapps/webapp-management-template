# Cloudflare — Security Baseline (alle proxied Zonen)
#
# Aktiviert kostenlose Edge-Security-Features fur alle CF-Zonen, die wir
# verwalten. Free-Plan-kompatibel, benotigt KEIN WAF/Rate-Limit/Cache-Rule-Budget.
#
# Was hier konfiguriert ist:
#   - Bot Fight Mode (cloudflare_bot_management.fight_mode = true)
#   - Leaked Credentials Detection (cloudflare_leaked_credential_check.enabled = true)
#
# Manuelle Schritte (kein v5-Resource verfugbar):
#   - Page Shield Monitor: CF Dashboard -> Zone -> Security -> Page Shield -> toggle ON
#     pro Zone. Aktive Blocking-Policies waren cloudflare_page_shield_policy (Pro+).
#
# Was deferred ist (separater Folge-Task):
#   - CF Notifications (cloudflare_notification_policy): braucht zuerst
#     Notification-Destinations (Email/Webhook IDs aus CF Dashboard).
#
# Verifizierung nach `terraform apply`:
#   1. CF Dashboard pro Zone -> Security -> Bots -> "Bot Fight Mode" toggled ON.
#   2. CF Dashboard pro Zone -> Security -> Settings -> "Leaked credentials check"
#      = Enabled (Detection-only). Bei Match wird Header
#      `Exposed-Credential-Check: 1` gesetzt.
#
# TEMPLATE: Replace cf_security_zones with your actual zone data sources.
# Account-level resources use var.cloudflare_account_id (never hardcode).

# ---------------------------------------------------------------------------
# EXAMPLE zone data sources — add one block per domain you manage.
# Rename the label (e.g. "your_zone") to something meaningful.
# ---------------------------------------------------------------------------
# data "cloudflare_zone" "your_zone" {
#   account_id = var.cloudflare_account_id
#   name       = var.domain
# }

locals {
  # Map aller produktiven Zonen, auf die das Security-Baseline angewendet wird.
  # Schluessel = sprechender Name, Wert = Zone-ID aus data source (kein Hardcode).
  # Beim Hinzufugen einer neuen Zone hier eintragen — Baseline wird automatisch
  # mit-aktiviert.
  #
  # TEMPLATE: replace with your actual data source references, e.g.:
  #   your_zone = data.cloudflare_zone.your_zone.id
  cf_security_zones = {
    # YOUR_ZONE_LABEL = data.cloudflare_zone.YOUR_ZONE_DATA_SOURCE.id
  }
}

# === Bot Fight Mode ===
# Free-Tier-Variante. Blockt bekannte schlechte Bots am Edge (Scraper,
# Header-Spoofer, etc.) — keine Konfiguration notig, single toggle.
#
# cloudflare_bot_management deckt Free (fight_mode), Pro+ (Super Bot Fight Mode)
# und Enterprise (Full Bot Management) ab. Auf Free-Plan ist nur `fight_mode`
# direkt setzbar; using_latest_model + andere Felder sind computed (vom
# API geliefert) und durfen im TF nicht gesetzt werden.
resource "cloudflare_bot_management" "fight_mode" {
  for_each = local.cf_security_zones
  zone_id  = each.value

  # Free-tier core toggle — blockt eindeutig bosartigen Bot-Traffic am Edge.
  fight_mode = true

  # v5-Schema verlangt explizite Defaults fur alle Felder, sonst kollidiert
  # Provider-Refresh mit v4-State. Alle Pro+/Enterprise-Features sind hier
  # bewusst auf "disabled"/false — wir aktivieren nur Bot Fight Mode (Free).
  ai_bots_protection      = "disabled"
  content_bots_protection = "disabled"
  crawler_protection      = "disabled"
  enable_js               = true
  is_robots_txt_managed   = false
  optimize_wordpress      = false
}

# === Leaked Credentials Detection ===
# Free-Tier-Feature. CF scannt eingehende Login-Requests gegen bekannte
# Credential-Leak-Datenbanken (HIBP & Co). Bei Match: HTTP-Header
# `Exposed-Credential-Check: 1` wird gesetzt — keine Blockierung am Edge.
resource "cloudflare_leaked_credential_check" "detection" {
  for_each = local.cf_security_zones
  zone_id  = each.value
  enabled  = true
}
