# ------------------------------------------------------------------------------------------------
# EC2 host role: pull images, ship logs/metrics, read secrets, and let NexOps read CloudWatch/EC2/ECR.
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${var.project}-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "host_managed" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.host.name
  policy_arn = each.value
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "ReadDeployBundle"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"]
  }
  statement {
    sid       = "WriteDatabaseBackups"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.bundle.arn}/backups/*"]
  }
  statement {
    sid       = "ReadNexOpsSecrets"
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/${var.project}/*"]
  }
  statement {
    sid = "NexOpsObservabilityRead"
    actions = [
      "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
      "logs:FilterLogEvents", "logs:DescribeLogGroups", "logs:GetLogEvents",
      "ec2:DescribeInstances",
      "ecr:DescribeRepositories", "ecr:DescribeImages",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "ShipContainerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for group in aws_cloudwatch_log_group.this : "${group.arn}:*"]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${var.project}-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${var.project}-host"
  role = aws_iam_role.host.name
}

# ------------------------------------------------------------------------------------------------
# GitHub Actions OIDC: no long-lived AWS keys in GitHub.
locals {
  github_owner = split("/", var.github_repository)[0]
  github_name  = split("/", var.github_repository)[1]
  # GitHub issues either the classic subject (repo:owner/name) or, for newer repositories, an immutable one that pins
  # the numeric owner and repository ids (repo:owner@123/name@456). Trust both when the ids are known.
  github_subject_prefixes = concat(
    ["repo:${var.github_repository}"],
    var.github_owner_id != null && var.github_repository_id != null ? ["repo:${local.github_owner}@${var.github_owner_id}/${local.github_name}@${var.github_repository_id}"] : [],
  )
  github_subjects = flatten([
    for prefix in local.github_subject_prefixes : ["${prefix}:ref:refs/heads/${var.github_branch}", "${prefix}:environment:production"]
  ])
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_subjects
    }
  }
}

resource "aws_iam_role" "github" {
  name               = "${var.project}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload", "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
    ]
    resources = [for repository in aws_ecr_repository.this : repository.arn]
  }
  statement {
    sid       = "UploadBundle"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetObject"]
    resources = [aws_s3_bucket.bundle.arn, "${aws_s3_bucket.bundle.arn}/*"]
  }
  statement {
    sid       = "RunDeployCommands"
    actions   = ["ssm:SendCommand"]
    resources = [aws_instance.host.arn, "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }
  statement {
    sid       = "ReadCommandResults"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ec2:DescribeInstances"]
    resources = ["*"]
  }
  # DEPLOY_TARGET=eks: kubeconfig for the on-demand cluster from infra/terraform/eks. Kubernetes permissions come
  # from its access entry (group nexops-deployers -> deploy/k8s/overlays/eks/deployer-rbac.yaml).
  statement {
    sid       = "DescribeEksCluster"
    actions   = ["eks:DescribeCluster"]
    resources = ["arn:aws:eks:${var.region}:${local.account_id}:cluster/${var.project}-eks"]
  }
}

resource "aws_iam_role_policy" "github" {
  name   = "${var.project}-github-deploy"
  role   = aws_iam_role.github.id
  policy = data.aws_iam_policy_document.github.json
}
