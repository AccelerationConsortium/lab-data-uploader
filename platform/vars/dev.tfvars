app_name    = "lab-data-uploader"
environment = "dev"

# Container configuration
container_port = 8000
cpu            = 512
memory         = 1024
desired_count  = 1

# Health check
health_check_path     = "/health"
health_check_interval = 30
health_check_timeout  = 5

# No public access needed — internal service
enable_cloudfront       = false
allow_cloudfront_access = false
enable_waf              = false
rate_limit              = 0
enable_cognito_auth     = false

# Environment variables
environment_variables = {
  ENVIRONMENT = "dev"
  NFS_MOUNTS  = "100.115.219.51:/labdata:/mnt/lab1"
}

# Secrets (stored in AWS Secrets Manager)
secrets = {
  TS_AUTHKEY = "/lab-data-uploader/tailscale-authkey"
}

init_container_command = []
