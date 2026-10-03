variable "project" {
  description = "Name prefix for every resource (also the SSM parameter path /<project>/...)."
  type        = string
  default     = "nexops"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy through OIDC, as owner/name."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub owner id. With github_repository_id, trusts GitHub's immutable OIDC subject (repo:owner@id/name@id). Find it: gh api repos/OWNER/REPO --jq .owner.id"
  type        = number
  default     = null
}

variable "github_repository_id" {
  description = "Numeric GitHub repository id (gh api repos/OWNER/REPO --jq .id)."
  type        = number
  default     = null
}

variable "github_branch" {
  description = "Branch whose workflows may assume the deploy role (workflow_dispatch rollbacks run on this ref too)."
  type        = string
  default     = "main"
}

variable "allowed_cidrs" {
  description = "CIDR blocks allowed to reach the NexOps web UI/API on port 80. Restrict to your office/VPN IPs."
  type        = list(string)
}

variable "webhook_cidrs" {
  description = "Extra CIDRs allowed on port 80 for CI webhooks. GitHub-hosted runners use many ranges; use 0.0.0.0/0 only if the API is otherwise protected (HMAC-signed webhooks + login)."
  type        = list(string)
  default     = []
}

variable "instance_type" {
  description = "EC2 instance type for the Docker host. t3.small (2 GiB) fits the whole stack (~700 MiB) and is Free Tier eligible."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GiB (30 GiB is the Free Tier EBS allowance)."
  type        = number
  default     = 30
}

variable "detailed_monitoring" {
  description = "Enable 1-minute EC2 CloudWatch metrics (extra cost)."
  type        = bool
  default     = false
}

variable "admin_email" {
  description = "First NexOps administrator email."
  type        = string
}

variable "groq_api_key" {
  description = "Optional Groq API key for LLM investigations (stored as an SSM SecureString)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "github_token" {
  description = "Optional GitHub token for NexOps (commit diffs + rollback workflow dispatch)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "enable_rds" {
  description = "Use Amazon RDS PostgreSQL for the NexOps database instead of a container on the host."
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "RDS instance class when enable_rds = true."
  type        = string
  default     = "db.t4g.micro"
}

variable "backup_retention_days" {
  description = "Days to keep nightly NexOps database backups in S3."
  type        = number
  default     = 14
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 14
}

# hosted.py down/up delete and re-create the whole stack. These keep the login and the GitHub webhook secret the
# same across re-creates (saved in deploy/.env as NEXOPS_CLOUD_ADMIN_PASSWORD / NEXOPS_CLOUD_WEBHOOK_SECRET).
variable "admin_password" {
  description = "NexOps admin password; empty = generated."
  type        = string
  default     = ""
  sensitive   = true
}

variable "webhook_secret" {
  description = "CI webhook HMAC secret (GitHub secret NEXOPS_WEBHOOK_SECRET); empty = generated."
  type        = string
  default     = ""
  sensitive   = true
}
