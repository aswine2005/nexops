variable "project" {
  type    = string
  default = "nexops"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version. Use one in standard support: versions in extended support cost 6x more per hour."
  type        = string
  default     = "1.36"
}

variable "node_instance_types" {
  description = "Free-plan accounts may only launch free-tier-eligible types (c7i-flex.large, m7i-flex.large, t3.small...)."
  type        = list(string)
  default     = ["c7i-flex.large"]
}

variable "node_count" {
  description = "Two c7i-flex.large nodes fit NexOps, Prometheus, Jaeger, Argo Rollouts and a 4-replica canary (VPC CNI allows 29 pods per node)."
  type        = number
  default     = 2
}

variable "endpoint_public_access_cidrs" {
  description = "Who can reach the Kubernetes API (IAM authentication still applies). GitHub-hosted runners need 0.0.0.0/0."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "admin_principal_arns" {
  description = "Extra IAM principals granted cluster-admin. The identity running Terraform is always admin."
  type        = list(string)
  default     = []
}

variable "github_deploy_role_arn" {
  description = "GitHub Actions deploy role from the aws stack (output github_role_arn); mapped to the nexops-deployers group."
  type        = string
  default     = ""
}
