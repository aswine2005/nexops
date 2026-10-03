# Fixed public IPs for the NexOps entry point: the AWS Load Balancer Controller attaches them to an internet-facing
# Network Load Balancer (one per public subnet). A fixed IP gives the UI the name <ip>.sslip.io, so Caddy in the
# cluster can get a Let's Encrypt certificate without a domain. (An ALB has no fixed IPs and needs an ACM
# certificate, which needs a domain you own.)
resource "aws_eip" "edge" {
  count  = length(local.azs)
  domain = "vpc"
  tags   = { Name = "${local.name}-edge-${count.index}" }
}
