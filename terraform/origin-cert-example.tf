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
  csr                  = tls_cert_request.origin_example_com.cert_request_pem
  hostnames            = ["example.com", "*.example.com"]
  request_type         = "origin-rsa"
  requested_validity   = 5475
}

# ---------------------------------------------------------------------------
# Outputs — write cert + key to local files so the server can consume them.
# Adjust paths to match your deployment layout.
# ---------------------------------------------------------------------------
resource "local_file" "origin_cert_example_com" {
  content  = cloudflare_origin_ca_certificate.origin_example_com.certificate
  filename = "${path.module}/certs/origin-example-com.pem"
}

resource "local_file" "origin_key_example_com" {
  sensitive_content = tls_private_key.origin_example_com.private_key_pem
  filename          = "${path.module}/certs/origin-example-com-key.pem"
}
