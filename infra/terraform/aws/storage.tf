# Deployment bundles (compose files, configs) uploaded by GitHub Actions and synced by the host.
resource "aws_s3_bucket" "bundle" {
  bucket_prefix = "${var.project}-deploy-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "bundle" {
  bucket                  = aws_s3_bucket.bundle.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  rule {
    id     = "expire-database-backups"
    status = "Enabled"
    filter {
      prefix = "backups/"
    }
    expiration {
      days = var.backup_retention_days
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(local.log_groups)
  name              = each.value
  retention_in_days = var.log_retention_days
}

# ------------------------------------------------------------------------------------------------
# Secrets in SSM Parameter Store, rendered into the host's .env at deploy time.
resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "random_password" "webhook_secret" {
  length  = 48
  special = false
}

resource "random_password" "admin_password" {
  length  = 20
  special = false
}

resource "random_password" "nexops_db" {
  length  = 32
  special = false
}

resource "random_password" "payments_db" {
  length  = 32
  special = false
}

resource "random_password" "flags_token" {
  length  = 40
  special = false
}

resource "random_password" "grafana" {
  length  = 20
  special = false
}

locals {
  secure_parameters = merge(
    {
      NEXOPS_SECRET_KEY      = random_password.secret_key.result
      NEXOPS_WEBHOOK_SECRET  = var.webhook_secret != "" ? var.webhook_secret : random_password.webhook_secret.result
      NEXOPS_ADMIN_PASSWORD  = var.admin_password != "" ? var.admin_password : random_password.admin_password.result
      NEXOPS_DB_PASSWORD     = random_password.nexops_db.result
      PAYMENTS_DB_PASSWORD   = random_password.payments_db.result
      GRAFANA_ADMIN_PASSWORD = random_password.grafana.result
      NEXOPS_FLAGS_TOKEN     = random_password.flags_token.result
    },
    var.groq_api_key != "" ? { GROQ_API_KEY = var.groq_api_key } : {},
    var.github_token != "" ? { GITHUB_TOKEN = var.github_token } : {},
    var.enable_rds ? { DATABASE_URL = "postgresql+psycopg://nexops:${random_password.nexops_db.result}@${aws_db_instance.nexops[0].address}:5432/nexops" } : {},
  )
  plain_parameters = {
    NEXOPS_ADMIN_EMAIL = var.admin_email
  }
}

resource "aws_ssm_parameter" "secure" {
  for_each = nonsensitive(toset(keys(local.secure_parameters)))
  name     = "/${var.project}/${each.value}"
  type     = "SecureString"
  value    = local.secure_parameters[each.value]
}

resource "aws_ssm_parameter" "plain" {
  for_each = local.plain_parameters
  name     = "/${var.project}/${each.key}"
  type     = "String"
  value    = each.value
}
