# CI/CD pipelines

These are the GitHub Actions pipelines NexOps is built and delivered with. In this showcase repository they are kept
here as code and **do not run**; to use them in your own repository, copy them into `.github/workflows/` and set the
repository variables and secret listed in [docs/ENVIRONMENT.md](../../docs/ENVIRONMENT.md).

| Pipeline | Trigger | What it does |
|---|---|---|
| [`ci.yml`](ci.yml) | every push and pull request | backend tests, migrations (upgrade / check / downgrade) on PostgreSQL, frontend type-check and build, `pip-audit` of the sample service, container builds, `terraform fmt` and `validate` for both stacks, renders every Kustomize overlay |
| [`deploy-nexops.yml`](deploy-nexops.yml) | changes to the platform | builds the API and web images, pushes them to ECR through GitHub OIDC (no stored AWS keys), uploads the deployment bundle to S3 and rolls it out on the EC2 host through SSM |
| [`deploy-payment-api.yml`](deploy-payment-api.yml) | changes to the sample service, or `workflow_dispatch` | reports each stage to NexOps, asks the NexOps deploy gate, tests, scans dependencies, builds and pushes the image, deploys to EC2 (SSM) or EKS (Argo Rollouts canary judged by NexOps), and serves as the rollback target NexOps triggers with `rollback=true` |
