# Example Cloudflare Origin Certificate resource.
# Copy to origin-cert-<domain>.tf per domain. See docs/ONBOARDING.md A.7

# ---------------------------------------------------------------------------
# Private key for the origin certificate (generated locally, stored in state).
# ---------------------------------------------------------------------------
resource "tls_private_key" "origin_example_com" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# ---------------------------------------------------------------------------
# Certificate Signing Request
# Replace example.com and *.example.com with your actual domain.
# ---------------------------------------------------------------------------
resource "tls_cert_request" "origin_example_com" {
  private_key_pem = tls_private_key.origin_example_com.private_key_pem

  subject {
    common_name = "example.com"
  }

  dns_names = [
    "example.com",
    "*.example.com",
  ]
}

# ---------------------------------------------------------------------------
# Cloudflare Origin CA Certificate
# Issued by Cloudflare — only valid for traffic through Cloudflare proxy.
# requested_validity: 5475 days (15 years, maximum for Origin CA).
# ---------------------------------------------------------------------------
resource "cloudflare_origin_ca_certificate" "origin_example_com" {
  csr = tls_cert_request.origin_example_com.cert_request_pem
  # v5: hostnames is list-order-sensitive (was a set in v4). Keep wildcard
  # FIRST to match how the value lands in state — otherwise apply churns
  # destroy+create on every plan.
  hostnames          = ["*.example.com", "example.com"]
  request_type       = "origin-rsa"
  requested_validity = 5475 # 15 years (Origin CA max)
}

# ---------------------------------------------------------------------------
# Outputs — cert + key as SENSITIVE outputs (not local files).
# Canonical delivery (matches the live platform): the private key never
# touches local disk. After `terraform apply`, extract via
#   terraform output -raw origin_example_com_cert
#   terraform output -raw origin_example_com_key
# and store BOTH in Proton Pass (domain-<domain>/origin_cert + origin_key).
# `sync-secrets --server` then pushes them as GitHub Secrets
# (ORIGIN_CERT_<SLUG> / ORIGIN_KEY_<SLUG>); the Deploy Infrastructure workflow
# writes ./certs/<slug>.crt|.key on the server. See docs/ONBOARDING.md A.0.6.
#
# NOTE: the CF API returns the key only ONCE — back it up in Proton right after
# the first apply.
# ---------------------------------------------------------------------------
output "origin_example_com_cert" {
  description = "Origin CA Certificate PEM (Multi-SAN). Store in Proton domain-<domain>/origin_cert."
  value       = cloudflare_origin_ca_certificate.origin_example_com.certificate
  sensitive   = true
}

output "origin_example_com_key" {
  description = "Origin private key PEM. Store in Proton domain-<domain>/origin_key. CF returns it only once."
  value       = tls_private_key.origin_example_com.private_key_pem
  sensitive   = true
}
