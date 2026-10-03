# NexOps on AWS: what exists, what it costs, how to remove it

Account `<account-id>`, region `ap-south-1` (Mumbai). Everything below was created by Terraform from
[`infra/terraform/aws`](../infra/terraform/aws) and is tagged `Project=nexops`, `ManagedBy=terraform`.
Terraform keeps its state in the S3 bucket `nexops-tfstate-<account-id>` (versioned, encrypted, locked with S3 lock files; see `backend.hcl`).
That bucket is deliberately **not** managed by the stack, so `terraform destroy` can never delete its own state.

## Local vs hosted

| | Local (your Mac) | Hosted (AWS) |
|---|---|---|
| URL | http://localhost:8080 | `https://<ip>.sslip.io`, printed by `hosted.py up` (new IP each time) |
| Runs on | Docker Desktop, compose project `nexops` | EC2 `<instance-id>`, compose projects `nexops` + `apps` |
| Monitored service | Local Payment API (`scripts/deploy_local.py`) | Payment API deployed by GitHub Actions |
| Deployments come from | `scripts/deploy_local.py` | `ci/github-actions/deploy-payment-api.yml` |
| Admin login | `deploy/.env` | `aws ssm get-parameter --name /nexops/NEXOPS_ADMIN_PASSWORD --with-decryption …` |
| Available when | Docker Desktop is running and the `nexops` containers are started | The EC2 instance is running (containers restart automatically) |

They are independent copies with separate databases. The old `backend` compose project (PostgreSQL on port 5432)
belongs to the original prototype and is not used.

## Resources (50 managed by Terraform)

| Group | Resources |
|---|---|
| Network | VPC `10.40.0.0/16`, internet gateway, 2 public + 2 private subnets, route table + 2 associations, security group (ports 80 and 443) |
| Compute | EC2 `t3.small` (Amazon Linux 2023, 30 GiB encrypted gp3, 1 GiB swap), Elastic IP `<elastic-ip>` |
| Images | ECR repositories `nexops-api`, `nexops-web`, `payment-api`, `traffic-generator` (scan on push, keep 10 images) |
| Logs | CloudWatch log groups `/nexops/platform`, `/nexops/payment-api` (14-day retention) |
| Storage | S3 bucket `nexops-deploy-…` (deployment bundle only; no database backups in AWS: `hosted.py down` saves the data into the Docker container `nexops-cloud-data` on your Mac) |
| Secrets | SSM Parameter Store `/nexops/*`: admin email/password, session key, webhook secret, DB passwords, Grafana password, feature-flags token, Groq key, GitHub token (plus `NEXOPS_NOTIFY_WEBHOOK_URL`, added by hand) |
| Identity | IAM role + instance profile `nexops-host`; GitHub OIDC provider; IAM role `nexops-github-deploy` (trusted only for `<owner>/nexops` on `main` / environment `production`) |
| Generated values | 7 `random_password` values (stored only in SSM and Terraform state) |

Not managed by Terraform: IAM user `nexops` and its access key (created by you), and the state bucket `nexops-tfstate-<account-id>`.

Running on the host (containers): NexOps API, worker, web, PostgreSQL, Caddy (HTTPS), Prometheus, Loki, Alloy, Grafana, Jaeger,
Payment API, its PostgreSQL, the traffic generator. Grafana, Prometheus and Jaeger listen only on localhost; reach them through
`terraform output grafana_tunnel_command` / `jaeger_tunnel_command`.

GitHub (not AWS): your repository, environment `production`, 7 repository variables,
secret `NEXOPS_WEBHOOK_SECRET`.

## Cost (deducted from Free plan credits)

| Item | Running | Instance stopped |
|---|---|---|
| EC2 `t3.small` | ~$16/month | $0 |
| EBS 30 GiB gp3 | ~$2.6/month | ~$2.6/month |
| Public IPv4 (Elastic IP) | ~$3.6/month | ~$3.6/month |
| CloudWatch, ECR, S3, SSM | <$2/month | <$1/month |

Stopping the instance keeps everything (data, IP, configuration); start it again and NexOps comes back by itself.

## On-demand EKS (separate stack, only while you use it)

`python3 scripts/k8s/cluster.py eks-up` creates a second, independent stack (`infra/terraform/eks`, state key
`nexops/eks.tfstate`): VPC `10.60.0.0/16` with 2 public subnets, EKS control plane `nexops-eks`, 2 × `c7i-flex.large`
nodes, EBS CSI / metrics-server / Pod Identity add-ons, IRSA roles, the AWS Load Balancer Controller and Argo Rollouts,
2 Elastic IPs with a Network Load Balancer (HTTPS on `<ip>.sslip.io` through Caddy) and three small gp3 volumes. About
**$0.30/hour**; `eks-down` removes all of it (load balancer and volumes first). Details: [KUBERNETES.md](KUBERNETES.md).

## Remove everything

```bash
cd infra/terraform/aws
terraform init -backend-config=backend.hcl
AWS_PROFILE=nexops terraform destroy        # deletes all 50 resources listed above
```

What is lost: the hosted NexOps database (incident and deployment history), Prometheus/Loki/Grafana data, ECR images,
CloudWatch logs and the SSM secrets. Nothing outside this list is touched (the default VPC and anything else in the
account stay).

Afterwards:
1. Delete the state bucket: `aws s3 rb s3://nexops-tfstate-<account-id> --force` (versioned objects may need emptying in the console), then the IAM user `nexops` (IAM → Users) or at least its access key.
2. In GitHub, delete the repository variables/secret or disable the deploy workflows (they can no longer reach AWS).
3. Locally, `docker compose -f deploy/docker-compose.yml --env-file deploy/.env down -v` removes the local stack and its data.
