terraform {
  required_version = ">= 1.9"
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.0" }
  }
  cloud {
    organization = "YOUR-TF-CLOUD-ORG"
    workspaces { name = "YOUR-WORKSPACE-NAME" }
  }
}

provider "cloudflare" { api_token = var.cloudflare_api_token }

# Separater Provider-Alias fuer Zero-Trust-Access-Resources. Resources die
# Apps/Policies/IdPs verwalten setzen provider = cloudflare.access, damit der
# Access-spezifische Token genutzt wird (siehe variables.tf).
provider "cloudflare" {
  alias     = "access"
  api_token = var.cloudflare_access_api_token
}

locals {
  ip_main_prod_v4 = "YOUR.MAIN.PROD.IP"
  ip_staging_v4   = "YOUR.STAGING.IP"
}
