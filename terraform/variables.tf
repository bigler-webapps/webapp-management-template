variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone and Tunnel permissions"
  type        = string
  sensitive   = true
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
