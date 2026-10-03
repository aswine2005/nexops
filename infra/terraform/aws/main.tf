data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  ecr_registry = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  azs          = slice(data.aws_availability_zones.available.names, 0, 2)
  repositories = ["${var.project}-api", "${var.project}-web", "payment-api", "traffic-generator"]
  log_groups   = ["/${var.project}/platform", "/${var.project}/payment-api"]
}
