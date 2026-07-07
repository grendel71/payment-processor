# Infrastructure — AWS / EKS Only

The Terraform modules and environments in this directory provision AWS cloud infrastructure:

- `modules/vpc/` — VPC, public/private subnets, NAT gateway
- `modules/eks/` — EKS cluster, node group, OIDC provider
- `modules/iam/` — Cluster, node, IRSA, and deploy roles
- `modules/ecr/` — Private image registry
- `environments/prod/` — Production environment composer

**These are not used for local Kubernetes deployment.**

For local Kubernetes (Talos homelab), all manifests live in `../k8s/` and are
managed by FluxCD. See `../k8s/kustomization.yaml` for the app resource list.
