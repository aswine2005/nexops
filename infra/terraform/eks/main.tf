# On-demand EKS for demos: create with `python3 scripts/k8s/cluster.py eks-up`, destroy with `eks-down`.
# Cost while it exists (ap-south-1, approx.): EKS control plane $0.10/h + 2 x c7i-flex.large + NLB + public IPv4.
# No NAT gateway: nodes run in public subnets with public IPs (security groups still block inbound traffic).

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project}-eks"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  admin_entries = {
    for index, arn in var.admin_principal_arns : "admin-${index}" => {
      principal_arn = arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
  # Kubernetes RBAC for this group lives in deploy/k8s/overlays/eks/deployer-rbac.yaml (patch the Rollout image,
  # port-forward to the NexOps API to report the release).
  deploy_entries = var.github_deploy_role_arn == "" ? {} : {
    github-deploy = {
      principal_arn     = var.github_deploy_role_arn
      kubernetes_groups = ["nexops-deployers"]
    }
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.21"

  name           = "${local.name}-vpc"
  cidr           = "10.60.0.0/16"
  azs            = local.azs
  public_subnets = [for index, _ in local.azs : cidrsubnet("10.60.0.0/16", 4, index)]

  map_public_ip_on_launch = true
  enable_nat_gateway      = false
  public_subnet_tags      = { "kubernetes.io/role/elb" = 1 }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.37"

  cluster_name    = local.name
  cluster_version = var.kubernetes_version
  # Upgrade at the end of standard support instead of paying for extended support.
  cluster_upgrade_policy = { support_type = "STANDARD" }

  cluster_endpoint_public_access           = true
  cluster_endpoint_public_access_cidrs     = var.endpoint_public_access_cidrs
  enable_cluster_creator_admin_permissions = true
  enable_irsa                              = true
  # Demo cluster: no customer-managed KMS key (it would bill for 30 days after deletion) and short log retention.
  create_kms_key              = false
  cluster_encryption_config   = {}
  cluster_enabled_log_types   = []
  create_cloudwatch_log_group = false

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
    metrics-server         = {}
    aws-ebs-csi-driver     = { service_account_role_arn = module.ebs_csi_irsa.iam_role_arn }
  }

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.node_instance_types
      capacity_type  = "ON_DEMAND"
      min_size       = 1
      max_size       = var.node_count + 1
      desired_size   = var.node_count
      disk_size      = 20
    }
  }

  access_entries = merge(local.admin_entries, local.deploy_entries)
}

module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.55"

  role_name             = "${local.name}-ebs-csi"
  attach_ebs_csi_policy = true
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

module "lb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.55"

  role_name                              = "${local.name}-lb-controller"
  attach_load_balancer_controller_policy = true
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }
}

# IAM role for the NexOps service account (CloudWatch/EC2/ECR read) through IRSA.
data "aws_iam_policy_document" "nexops_read" {
  statement {
    actions = [
      "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
      "logs:FilterLogEvents", "logs:DescribeLogGroups", "logs:GetLogEvents",
      "ec2:DescribeInstances", "ecr:DescribeRepositories", "ecr:DescribeImages",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "nexops_read" {
  name   = "${local.name}-nexops-read"
  policy = data.aws_iam_policy_document.nexops_read.json
}

module "nexops_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.55"

  role_name = "${local.name}-nexops"
  role_policy_arns = {
    read = aws_iam_policy.nexops_read.arn
  }
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["nexops:nexops"]
    }
  }
}
