# Cluster add-ons installed with Helm in the second apply (see versions.tf).

# Turns the NexOps Ingress into an internet-facing Application Load Balancer.
resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "3.5.0"
  wait       = true

  values = [yamlencode({
    clusterName  = module.eks.cluster_name
    region       = var.region
    vpcId        = module.vpc.vpc_id
    replicaCount = 1
    serviceAccount = {
      name        = "aws-load-balancer-controller"
      annotations = { "eks.amazonaws.com/role-arn" = module.lb_controller_irsa.iam_role_arn }
    }
  })]

  depends_on = [module.eks]
}

# Progressive delivery: the payment-api Rollout's canary steps, with NexOps as the analysis provider.
resource "helm_release" "argo_rollouts" {
  name             = "argo-rollouts"
  namespace        = "argo-rollouts"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-rollouts"
  version          = "2.43.2"
  wait             = true

  values = [yamlencode({
    controller = { replicas = 1 }
    dashboard  = { enabled = false }
  })]

  depends_on = [module.eks]
}
