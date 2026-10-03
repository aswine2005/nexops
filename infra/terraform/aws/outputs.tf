output "nexops_url" {
  description = "NexOps UI/API over HTTPS (set GitHub variable NEXOPS_URL to this). Uses <ip>.sslip.io unless /<project>/NEXOPS_DOMAIN is set in SSM."
  value       = "https://${replace(aws_eip.host.public_ip, ".", "-")}.sslip.io"
}

output "instance_id" {
  description = "EC2 host (GitHub variable EC2_INSTANCE_ID)."
  value       = aws_instance.host.id
}

output "github_role_arn" {
  description = "Role for GitHub Actions OIDC (GitHub variable AWS_ROLE_ARN)."
  value       = aws_iam_role.github.arn
}

output "ecr_registry" {
  description = "ECR registry host (GitHub variable ECR_REGISTRY)."
  value       = local.ecr_registry
}

output "bundle_bucket" {
  description = "S3 bucket for deployment bundles (GitHub variable DEPLOY_BUCKET)."
  value       = aws_s3_bucket.bundle.bucket
}

output "ssm_parameter_path" {
  description = "Secrets path in SSM Parameter Store."
  value       = "/${var.project}/"
}

output "admin_password_command" {
  description = "Read the generated NexOps admin password."
  value       = "aws ssm get-parameter --region ${var.region} --name /${var.project}/NEXOPS_ADMIN_PASSWORD --with-decryption --query Parameter.Value --output text"
}

output "webhook_secret_command" {
  description = "Read the webhook secret to store as the GitHub secret NEXOPS_WEBHOOK_SECRET."
  value       = "aws ssm get-parameter --region ${var.region} --name /${var.project}/NEXOPS_WEBHOOK_SECRET --with-decryption --query Parameter.Value --output text"
}

output "jaeger_tunnel_command" {
  description = "Open the Jaeger UI locally through SSM (trace links in NexOps point to http://localhost:16686)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.host.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=16686,localPortNumber=16686"
}

output "grafana_tunnel_command" {
  description = "Open Grafana/Prometheus locally through SSM (no public ports)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.host.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=3000,localPortNumber=3000"
}
