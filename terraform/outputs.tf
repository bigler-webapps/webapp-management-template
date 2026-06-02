output "tunnel_id_staging" {
  description = "Cloudflare Tunnel ID for the staging server"
  value       = cloudflare_tunnel.staging.id
}

output "tunnel_id_prod" {
  description = "Cloudflare Tunnel ID for the production server"
  value       = cloudflare_tunnel.prod.id
}
