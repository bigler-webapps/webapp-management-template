terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 4" }
  }
  cloud {
    organization = "YOUR-TF-CLOUD-ORG"
    workspaces { name = "YOUR-WORKSPACE-NAME" }
  }
}
provider "cloudflare" { api_token = var.cloudflare_api_token }
locals {
  ip_main_prod_v4 = "YOUR.MAIN.PROD.IP"
  ip_staging_v4   = "YOUR.STAGING.IP"
}
