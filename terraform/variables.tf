variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:Edit + DNS:Edit + Tunnel:Edit permissions"
  type        = string
  sensitive   = true
}

# Separater Token nur fuer Zero-Trust-Access-Resources (Apps + Policies Edit,
# Identity Providers Read). Wird vom provider-Alias cloudflare.access konsumiert.
# Defense-in-Depth: Blast-Radius von DNS/Tunnel und Access getrennt.
variable "cloudflare_access_api_token" {
  description = "Cloudflare API token scoped to Zero Trust Access (Apps + Policies Edit, IdPs Read)"
  type        = string
  sensitive   = true
  default     = ""  # empty until CF Access is configured; real value comes from TF Cloud workspace
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
}

variable "tunnel_secret_staging" {
  description = "Cloudflare Tunnel secret for staging server (base64, 32-byte random)"
  type        = string
  sensitive   = true
}

variable "tunnel_secret_prod" {
  description = "Cloudflare Tunnel secret for production server (base64, 32-byte random)"
  type        = string
  sensitive   = true
}

variable "domain" {
  description = "Primary domain managed in this workspace"
  type        = string
  default     = "example.com"
}
