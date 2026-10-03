# Run NexOps online (AWS)

You use NexOps online now and then, so nothing stays in AWS between uses: **`up`** creates everything and brings your
data back, **`down`** saves your data on your Mac and deletes everything in AWS. Between uses AWS costs **$0**.

| | What | Address | Cost (ap-south-1) |
|---|---|---|---|
| **EC2** (the hosted NexOps) | one server running NexOps, Prometheus, Loki, Grafana, Jaeger and the sample payment-api; GitHub Actions deploys it | `https://<ip>.sslip.io`, printed by `up` (a **new IP each time**) | ~$0.03/hour while it exists, **$0 after `down`** |
| **EKS** (optional demo) | the Kubernetes version with Argo Rollouts canaries | printed by `eks-up` | ~$0.30/hour while it exists, $0 after `eks-down` |

Every command below is run from the project folder:

```bash
cd nexops
```

---

## 0. What you need (once)

| Need | How |
|---|---|
| AWS CLI profile `nexops` | already set up (`aws configure --profile nexops`, region `ap-south-1`) |
| Docker Desktop running | Terraform runs in a container, and your online data is kept in the container `nexops-cloud-data` |
| Python 3 | already on macOS |
| `deploy/.env` | holds `GITHUB_TOKEN` (`up` uses it to set the GitHub variables and run the deploy workflows), `GROQ_API_KEY`, `NEXOPS_NOTIFY_WEBHOOK_URL` (Slack), and after the first `down`/`save` your online login (`NEXOPS_CLOUD_ADMIN_PASSWORD`, `NEXOPS_CLOUD_WEBHOOK_SECRET`) |
| AWS Session Manager plugin | downloaded automatically into `.nexops-local/tools` the first time it is needed (for the database tunnel) |
| For EKS only | kubectl (comes with Docker Desktop); the IAM user's `nexops-eks` policy (done) |

## Your online login

| | |
|---|---|
| Email | the `admin_email` you set in `infra/terraform/aws/terraform.tfvars` |
| Password | `python3 scripts/aws/hosted.py login` prints it. It is also the `NEXOPS_CLOUD_ADMIN_PASSWORD` line in `deploy/.env`; `up` always re-creates the same one |

---

## Part 1: your routine

| # | Command | What happens |
|---|---|---|
| 1 | `python3 scripts/aws/hosted.py up` | (~20 min) creates the Terraform state bucket if missing → creates the 52 AWS resources → waits for the server to install Docker → puts the Slack URL from `deploy/.env` into SSM → updates the 7 GitHub variables (new IP, new instance) → runs **Deploy NexOps** → copies your saved data from the Docker container `nexops-cloud-data` into the new database (and runs migrations) → runs **Deploy payment-api** → registers payment-api. Prints the address |
| 2 | `python3 scripts/aws/hosted.py login` | address, email, password |
| 3 | open the printed `https://<ip>.sslip.io` | sign in and use it ([USER_MANUAL.md](USER_MANUAL.md), [TESTING.md](TESTING.md)) |
| 4 | `python3 scripts/aws/hosted.py down` | (~10 min) copies the online database into `nexops-cloud-data` over an encrypted tunnel and checks that every table matches → asks `[y/N]` → deletes EKS if it exists → deletes all 52 resources, the Slack SSM parameter and the state bucket → confirms nothing NexOps created is left |
| any time | `python3 scripts/aws/hosted.py status` | what exists in AWS and what is saved on your Mac |

Running `up` while it is already up is safe: nothing is re-created, it redeploys and does **not** overwrite the online
data (add `--restore` if you really want the saved copy back).

### Where your data is

| When | Where | Kept until |
|---|---|---|
| between `up` and `down` | the PostgreSQL container on the EC2 server | `down` |
| after `down` | the Docker container **`nexops-cloud-data`** on your Mac (PostgreSQL, `localhost:5434`, volume `nexops_cloud_data`) | you delete that container *with its volume* |
| backups in AWS | **none**: nothing is copied to S3 | — |

So if you register your own website, run tests, get incidents, and then `down`: all of it (applications, deployments,
incidents, postmortems, flags, policies, users, metric history) is saved in `nexops-cloud-data` and comes back with
the next `up`. Start/stop that container with the buttons in Docker Desktop like any other; only deleting it with
its volume loses the data. `python3 scripts/aws/hosted.py save` copies the online data at any moment without deleting
anything. To look at it with a database tool: host `localhost`, port `5434`, database/user `nexops`, password
`nexops_cloud_data_local_only` (it only listens on your Mac).

What is **not** kept: the sample payment-api's own payments table and the raw Prometheus/Loki/Jaeger data (NexOps
keeps its own metric samples, so the charts keep their history).

### Deploy online (GitHub Actions, while it is up)

| I want to… | Do this | What happens |
|---|---|---|
| deploy NexOps itself | push to `main` (changes in `backend/`, `src/`, `deploy/aws/` …) or GitHub → **Actions → Deploy NexOps → Run workflow** | builds the API and web images, pushes them to ECR, uploads `deploy/aws` to S3, and through SSM pulls and restarts the containers on the server (~3 min) |
| release payment-api | change something under `examples/payment-api/`, bump `VERSION`, push; or **Actions → Deploy payment-api → Run workflow** | reports *started* to NexOps, asks the deploy gate, tests, scans dependencies, builds and pushes the image, replaces the container on the server, reports *finished* |
| roll back by hand | **Actions → Deploy payment-api → Run workflow** with an earlier `version` and `rollback = true` | redeploys an existing image without building |

While it is down, these workflows are skipped (`down` sets the GitHub variable `HOSTED_UP=false`, `up` sets it back), so pushes never fail; the next `up` deploys the latest code. A fresh `up` has only the current
images in ECR, so rollbacks can only go to versions deployed since that `up`.

### Optional: Grafana and Jaeger on the server

They are not public. Get the instance ID from `status`, then open a tunnel and keep the terminal open:

```bash
PATH="$PWD/.nexops-local/tools/sessionmanager-bundle/bin:$PATH" aws ssm start-session --profile nexops --region ap-south-1 --target <instance-id> --document-name AWS-StartPortForwardingSession --parameters portNumber=16686,localPortNumber=16686
```

Then Jaeger is at http://localhost:16686. For Grafana use `portNumber=3000,localPortNumber=3000` → http://localhost:3000.

### What exists in AWS while it is up (52 resources, all from `infra/terraform/aws`)

| Group | Resources | Cost |
|---|---|---|
| Network | VPC `10.40.0.0/16`, internet gateway, 2 public + 2 private subnets, route table, security group (ports 80, 443) | free |
| Server | EC2 `t3.small` (Amazon Linux 2023, Docker, 30 GB encrypted disk, 1 GB swap, no SSH, managed through SSM), Elastic IP | ~$0.03/hour together |
| Images | ECR repositories `nexops-api`, `nexops-web`, `payment-api`, `traffic-generator` | cents |
| Logs | CloudWatch log groups `/nexops/platform`, `/nexops/payment-api` | cents |
| Storage | S3 bucket `nexops-deploy-…`: the deployment bundle only | ~$0 |
| Secrets | SSM parameters `/nexops/*`: admin login, keys, database passwords, flags token, Groq key, GitHub token (+ the Slack URL, put there by `up`) | free |
| Access | IAM role + instance profile for the server; GitHub OIDC provider; IAM role `nexops-github-deploy` (only `<owner>/nexops` on `main` may use it) | free |

Plus the Terraform state bucket `nexops-tfstate-<account-id>` (created by `up`, deleted by `down`).

### Short pause instead of down

`python3 scripts/aws/hosted.py stop` / `start` keeps the server, its IP and data in AWS (~$6/month while stopped:
disk and IP). Use it only if you need the same address the next day; otherwise `down` is cheaper (free).

### Change the setup

| Change | How |
|---|---|
| server size, allowed IPs… | edit `infra/terraform/aws/terraform.tfvars` or `*.tf`, then `python3 scripts/aws/hosted.py apply` while it is up (shows the plan, asks first), or just the next `up` |
| Slack URL, Groq key, GitHub token | change it in `deploy/.env`; the next `up` uses it (while up: run `up` again, which redeploys) |

---

## Part 2: EKS on demand

EKS reads the login and secrets from SSM and the NexOps images from ECR, so run `hosted.py up` first.

Do these in order. Always finish with `eks-down`.

| # | Command | What happens |
|---|---|---|
| 1 | `AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-up` | creates the cluster and deploys everything (~25 min, details below). Prints the HTTPS address |
| 2 | open the printed `https://<ip>.sslip.io` | same login as EC2. Only your current IP can reach it |
| 3 | `AWS_PROFILE=nexops python3 scripts/deploy_local.py v1.5.0 --target eks` | a canary release from your Mac (builds for x86, pushes to ECR, 25% → NexOps check → 50% → check → 100%) |
| 4 | `kubectl argo rollouts --context nexops-eks -n payments get rollout payment-api --watch` | watch the canary live |
| 5 | `AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-down` | deletes everything (~15 min) and stops the cost |

To demo from another network: `ALLOWED_CIDR=0.0.0.0/0 AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-up` (re-running is safe).
To release from GitHub instead of step 3: set the repository variable `DEPLOY_TARGET=eks` (Settings → Secrets and
variables → Actions → Variables) while the cluster exists, and set it back to `ec2` before step 5.

### What `eks-up` does, step by step

1. **Terraform, phase 1 (~15 min):** VPC `10.60.0.0/16` with 2 public subnets (no NAT gateway), the EKS control plane `nexops-eks` (Kubernetes 1.36), a node group of 2 × `c7i-flex.large`, add-ons (VPC CNI, CoreDNS, kube-proxy, Pod Identity, metrics-server, EBS CSI driver).
2. **Terraform, phase 2 (~3 min):** IAM roles for the load balancer controller, EBS driver and NexOps (IRSA), 2 Elastic IPs, Helm releases: AWS Load Balancer Controller and Argo Rollouts.
3. **Kubeconfig:** adds the context `nexops-eks` to `~/.kube/config`.
4. **Secrets:** reads `/nexops/*` from SSM and writes Kubernetes Secrets (nothing on disk).
5. **Apply manifests (~3 min):** NexOps (API, worker, web, PostgreSQL), Prometheus, Jaeger, the payment-api Rollout (4 pods) with its database and traffic generator, Caddy, and a `LoadBalancer` Service. The controller then creates a **Network Load Balancer** on the 2 Elastic IPs.
6. **HTTPS (~3–5 min):** Caddy gets a Let's Encrypt certificate for `<ip>.sslip.io`, then the load balancer is limited to your IP.
7. **Register:** payment-api is registered in this NexOps as an Argo Rollout with the `fraud-check` flag.

### What exists while EKS is up (61 Terraform resources plus what Kubernetes creates)

| Group | Resources | Cost |
|---|---|---|
| Control plane | EKS cluster `nexops-eks`, its OIDC provider, access entries (you as admin, the GitHub deploy role) | $0.10/hour |
| Nodes | managed node group, launch template, 2 × `c7i-flex.large` EC2 instances with public IPs | ~$0.15/hour |
| Network | VPC, 2 subnets, internet gateway, route tables, security groups | free |
| Entry point | 2 Elastic IPs, Network Load Balancer (created by the controller) | ~$0.04/hour |
| Storage | 3 gp3 EBS volumes: NexOps DB 2 GB, payments DB 1 GB, Caddy certificates 1 GB (created by the EBS driver) | ~$0.001/hour |
| Identity | IAM roles: cluster, nodes, EBS driver, load balancer controller, NexOps (read-only CloudWatch/EC2/ECR) | free |

### What `eks-down` does

1. Deletes the `LoadBalancer` Service and any Ingress, so the controller removes the load balancer and its security groups.
2. Deletes the databases and their volumes, so the EBS driver removes the disks.
3. `terraform destroy`: cluster, nodes, add-ons, Helm releases, IAM roles, Elastic IPs, VPC (61 resources).
4. Removes the `nexops-eks` kubectl context. EC2 is untouched.

Check that nothing is left (each should print nothing):

```bash
aws eks list-clusters --profile nexops --region ap-south-1 --query clusters --output text
```
```bash
aws elbv2 describe-load-balancers --profile nexops --region ap-south-1 --query 'LoadBalancers[].LoadBalancerName' --output text
```
```bash
aws ec2 describe-volumes --profile nexops --region ap-south-1 --filters Name=status,Values=available --query 'Volumes[].VolumeId' --output text
```

---

## Part 3: delete everything

| Goal | Command | What happens |
|---|---|---|
| stop paying, keep your data (normal) | `python3 scripts/aws/hosted.py down` | data saved in Docker on your Mac, everything in AWS deleted |
| see first what would be deleted | `python3 scripts/aws/hosted.py down --preview` | the full list; deletes nothing |
| stop paying and forget the online data | `python3 scripts/aws/hosted.py destroy` | asks you to type `delete nexops`; deletes everything in AWS without saving |
| also forget the saved copy | Docker Desktop → Containers → `nexops-cloud-data` → delete, and Volumes → `nexops_cloud_data` → delete | the saved online data is gone |

After `down` or `destroy` nothing NexOps created is left in AWS, so there is nothing to remove by hand. Only if you
give up on AWS for this project altogether:

| Item | Where |
|---|---|
| IAM user `nexops` (access key, 7 attached policies, inline policy `nexops-eks`) | AWS console → IAM → Users → `nexops` → **Delete** (after it the CLI profile stops working; remove `[nexops]` from `~/.aws/credentials` and `[profile nexops]` from `~/.aws/config`) |
| Service-linked roles AWS made by itself (`AWSServiceRoleForAmazonEKS`, `…EKSNodegroup`, `…ElasticLoadBalancing`, `…AutoScaling`) | IAM → Roles; free, fine to leave |
| GitHub variables, secret `NEXOPS_WEBHOOK_SECRET`, environment `production` | GitHub → repo → Settings → Secrets and variables → Actions / Environments |
| GitHub token, Groq key | GitHub → Settings → Developer settings → tokens; console.groq.com → API keys |

---

## Check in the AWS console that everything is gone

Sign in at https://console.aws.amazon.com as the account owner. **First set the region (top right) to
Asia Pacific (Mumbai) `ap-south-1`**: most pages only show one region at a time.

**The one-page check:** *Resource Groups & Tag Editor* → *Tag Editor* → Regions: **All regions**, Resource types:
**All supported resource types**, Tags: key `Project`, value `nexops` → **Search resources**. After a full delete it
finds nothing. (Every NexOps resource made by Terraform carries that tag.)

Page by page:

| Console page | Where | After `eks-down` | After `hosted.py down` |
|---|---|---|---|
| EC2 → Instances | Instances | no `nexops-eks` nodes | no `nexops-host` (a *terminated* one may stay listed for an hour; that is normal and free) |
| EC2 → Elastic IPs | Network & Security | no `nexops-eks-edge-*` | no `nexops-host` |
| EC2 → Volumes | Elastic Block Store | none in state *available* | none |
| EC2 → Load Balancers | Load Balancing | no `k8s-nexops-*` | none |
| EC2 → Security Groups | Network & Security | only `default` ones and the EC2 stack's | only `default` ones |
| EKS → Clusters | Elastic Kubernetes Service | no `nexops-eks` | none |
| VPC → Your VPCs | VPC | no `nexops-eks-vpc` | no `nexops-vpc` (the **default** VPC stays; do not delete it) |
| VPC → NAT gateways | VPC | none (NexOps never creates one) | none |
| ECR → Repositories | Elastic Container Registry | unchanged | no `nexops-api`, `nexops-web`, `payment-api`, `traffic-generator` |
| S3 → Buckets (global) | S3 | unchanged | no `nexops-deploy-…`, no `nexops-tfstate-<account-id>` |
| CloudWatch → Log groups | Logs | unchanged | no `/nexops/platform`, `/nexops/payment-api` |
| Systems Manager → Parameter Store | Application Management | unchanged | nothing starting with `/nexops/` |
| IAM → Roles (global) | Access management | no `nexops-eks-*` | no `nexops-host`, `nexops-github-deploy` |
| IAM → Identity providers (global) | Access management | no `oidc.eks.ap-south-1…` | no `token.actions.githubusercontent.com` |
| IAM → Users (global) | Access management | `nexops` stays | `nexops` stays until **you** delete it (last step) |
| Billing → Bills | Billing and Cost Management | check the next day | no new NexOps charges from the day after |

Tag Editor needs the account owner's sign-in (the `nexops` IAM user is not allowed to search tags).

Same checks from the terminal with the `nexops` profile (each prints nothing when everything is gone):

```bash
aws ec2 describe-instances --profile nexops --region ap-south-1 --filters Name=tag:Project,Values=nexops Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text
```
```bash
aws eks list-clusters --profile nexops --region ap-south-1 --query clusters --output text
```
```bash
aws ec2 describe-addresses --profile nexops --region ap-south-1 --query 'Addresses[].PublicIp' --output text
```
```bash
aws elbv2 describe-load-balancers --profile nexops --region ap-south-1 --query 'LoadBalancers[].LoadBalancerName' --output text
```
```bash
aws ecr describe-repositories --profile nexops --region ap-south-1 --query 'repositories[].repositoryName' --output text
```
```bash
aws s3 ls --profile nexops | grep nexops
```


---
