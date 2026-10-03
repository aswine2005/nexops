terraform {
  required_version = ">= 1.10.0"

  # State lives next to the aws stack's (same bucket, key nexops/eks.tfstate); scripts/k8s/cluster.py passes it in.
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.95, < 6.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.project, ManagedBy = "terraform", Stack = "eks" }
  }
}

# A short-lived token (no aws CLI needed inside the Terraform container). cluster.py applies the cluster first and the
# Helm releases in a second apply, so the token is always fresh.
data "aws_eks_cluster_auth" "this" {
  name = module.eks.cluster_name
}

provider "helm" {
  kubernetes = {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}
