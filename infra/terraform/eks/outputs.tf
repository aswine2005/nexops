output "cluster_name" {
  value = module.eks.cluster_name
}

output "region" {
  value = var.region
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name} --alias nexops-eks"
}

output "nexops_service_account_role_arn" {
  description = "IRSA role for the nexops ServiceAccount (filled into deploy/k8s/overlays/eks by cluster.py)."
  value       = module.nexops_irsa.iam_role_arn
}

output "edge_subnet_ids" {
  description = "Public subnets for the NexOps Network Load Balancer, in the same order as edge_eip_allocation_ids."
  value       = join(",", module.vpc.public_subnets)
}

output "edge_eip_allocation_ids" {
  value = join(",", aws_eip.edge[*].allocation_id)
}

output "nexops_url" {
  description = "HTTPS address of NexOps (certificate from Let's Encrypt for the first Elastic IP)."
  value       = "https://${replace(aws_eip.edge[0].public_ip, ".", "-")}.sslip.io"
}
