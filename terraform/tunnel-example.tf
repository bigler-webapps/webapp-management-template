# Example Cloudflare Tunnel resource pattern.
# Copy to tunnel-<server>.tf per server. See docs/ONBOARDING.md A.7

# ---------------------------------------------------------------------------
# Zone data source — one per domain managed by this workspace.
# ---------------------------------------------------------------------------
# data "cloudflare_zone" "your_zone" {
#   account_id = var.cloudflare_account_id
#   name       = var.domain
# }

# ---------------------------------------------------------------------------
# Tunnel resource
# Replace YOUR_SERVER_NAME and YOUR-TUNNEL-NAME with real values.
# tunnel_secret must come from a TF variable (sensitive), never hardcoded.
# ---------------------------------------------------------------------------
resource "cloudflare_tunnel" "YOUR_SERVER_NAME" {
  account_id = var.cloudflare_account_id
  name       = "YOUR-TUNNEL-NAME"
  secret     = var.tunnel_secret_prod # or var.tunnel_secret_staging
}

# ---------------------------------------------------------------------------
# DNS CNAME — points the domain to the tunnel ingress endpoint.
# ---------------------------------------------------------------------------
resource "cloudflare_record" "tunnel_YOUR_SERVER_NAME" {
  zone_id = data.cloudflare_zone.your_zone.id
  name    = "@" # or a subdomain, e.g. "app"
  type    = "CNAME"
  content = "${cloudflare_tunnel.YOUR_SERVER_NAME.id}.cfargotunnel.com"
  proxied = true
}

# ---------------------------------------------------------------------------
# Tunnel config — maps ingress rules to backend services.
# ---------------------------------------------------------------------------
resource "cloudflare_tunnel_config" "YOUR_SERVER_NAME" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_tunnel.YOUR_SERVER_NAME.id

  config {
    ingress_rule {
      hostname = var.domain
      service  = "http://localhost:8000"
    }
    # Catch-all — required by Cloudflare; returns 404 for unmatched hostnames.
    ingress_rule {
      service = "http_status:404"
    }
  }
}
