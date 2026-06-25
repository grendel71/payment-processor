# Terraform EKS Infrastructure Design

## Goal

Provision a production-ready AWS infrastructure for the payment processor using Terraform. The infrastructure hosts the FastAPI payment service on EKS, stores container images in ECR, and enforces least-privilege IAM throughout.

---

## Constraints And Standards

- Region: `us-east-1`
- Single environment: `prod`
- Remote state: S3 + DynamoDB lock
- Terraform standards: `modules/` + `environments/` structure, reusable variables, documented outputs, encryption enabled, logging enabled
- Security standards: no hardcoded secrets, least-privilege IAM, no public node groups, encryption at rest and in transit
- Infrastructure as code only — no manual cloud resources

---

## Directory Structure

```
infra/
  bootstrap/
    main.tf           # S3 state bucket + DynamoDB lock table
    outputs.tf
    variables.tf

  modules/
    vpc/
      main.tf
      variables.tf
      outputs.tf
    eks/
      main.tf
      variables.tf
      outputs.tf
    ecr/
      main.tf
      variables.tf
      outputs.tf
    iam/
      main.tf
      variables.tf
      outputs.tf

  environments/
    prod/
      main.tf
      variables.tf
      outputs.tf
      backend.tf
      terraform.tfvars
```

---

## Module Specifications

### bootstrap/

**Purpose:** One-time manual apply to create the remote state backend resources. Not managed by itself.

Resources:
- `aws_s3_bucket.tf_state` — versioning enabled, AES-256 SSE, all public access blocked
- `aws_s3_bucket_versioning.tf_state`
- `aws_s3_bucket_server_side_encryption_configuration.tf_state`
- `aws_s3_bucket_public_access_block.tf_state`
- `aws_dynamodb_table.tf_lock` — `LockID` hash key, PAY_PER_REQUEST billing

Variables:
- `project` (string) — used to prefix all resource names
- `region` (string, default `us-east-1`)

Outputs:
- `state_bucket_name`
- `lock_table_name`

---

### modules/vpc/

**Purpose:** Isolated network for all payment platform resources.

Resources:
- `aws_vpc` — CIDR `10.0.0.0/16`, DNS hostnames and resolution enabled
- `aws_subnet` (public × 3) — `/24` subnets in `us-east-1a`, `us-east-1b`, `us-east-1c`; `map_public_ip_on_launch = false`
- `aws_subnet` (private × 3) — `/24` subnets in each AZ
- `aws_internet_gateway` — attached to VPC
- `aws_eip` × 3 — one per AZ, for NAT Gateways
- `aws_nat_gateway` × 3 — one per public subnet, using respective EIP
- `aws_route_table` (public) — default route `0.0.0.0/0` → IGW
- `aws_route_table` (private × 3) — default route per AZ → respective NAT Gateway
- `aws_route_table_association` × 6

Subnet tagging for EKS:
- Public subnets: `kubernetes.io/role/elb = 1`
- Private subnets: `kubernetes.io/role/internal-elb = 1`
- All subnets: `kubernetes.io/cluster/<cluster_name> = shared`

Variables:
- `project` (string)
- `vpc_cidr` (string, default `10.0.0.0/16`)
- `availability_zones` (list(string), default `["us-east-1a","us-east-1b","us-east-1c"]`)
- `public_subnet_cidrs` (list(string), default `["10.0.1.0/24","10.0.2.0/24","10.0.3.0/24"]`)
- `private_subnet_cidrs` (list(string), default `["10.0.11.0/24","10.0.12.0/24","10.0.13.0/24"]`)
- `cluster_name` (string) — for subnet tags

Outputs:
- `vpc_id`
- `public_subnet_ids` (list)
- `private_subnet_ids` (list)

---

### modules/iam/

**Purpose:** All IAM roles and policies for EKS, nodes, IRSA, and CI deployment.

Resources:

**EKS Cluster Role**
- `aws_iam_role.eks_cluster` — assume-role policy for `eks.amazonaws.com`
- `aws_iam_role_policy_attachment` — `AmazonEKSClusterPolicy`

**EKS Node Role**
- `aws_iam_role.eks_node` — assume-role policy for `ec2.amazonaws.com`
- `aws_iam_role_policy_attachment` × 3:
  - `AmazonEKSWorkerNodePolicy`
  - `AmazonEKS_CNI_Policy`
  - `AmazonEC2ContainerRegistryReadOnly`

**Payments App IRSA Role**
- `aws_iam_role.payments_app` — assume-role with OIDC condition binding to K8s ServiceAccount `payments/payments-platform`
- `aws_iam_policy.payments_app` — allows:
  - `ecr:GetAuthorizationToken`
  - `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` on the payments ECR repo ARN
  - `secretsmanager:GetSecretValue` on ARN prefix `arn:aws:secretsmanager:us-east-1:<account_id>:secret:payments/*`
- `aws_iam_role_policy_attachment` — attaches above policy to IRSA role

**Terraform Deploy Role**
- `aws_iam_role.terraform_deploy` — assume-role for CI (scoped to specific principal via variable)
- `aws_iam_policy.terraform_deploy` — least-privilege set covering:
  - EC2 (VPC, subnets, SGs, IGW, NAT, EIP, route tables)
  - EKS (cluster and node group CRUD)
  - ECR (repository management)
  - IAM (only on resources prefixed with project name)
  - S3 (state bucket only)
  - DynamoDB (lock table only)
- `aws_iam_role_policy_attachment`

Variables:
- `project` (string)
- `cluster_name` (string)
- `oidc_provider_arn` (string) — from EKS module output
- `oidc_provider_url` (string) — from EKS module output
- `ecr_repository_arn` (string) — from ECR module output
- `ci_principal_arn` (string) — ARN of CI user/role that may assume the deploy role
- `aws_account_id` (string)

Outputs:
- `eks_cluster_role_arn`
- `eks_node_role_arn`
- `payments_app_role_arn`
- `terraform_deploy_role_arn`

---

### modules/eks/

**Purpose:** EKS control plane, managed node group, OIDC provider.

Resources:
- `aws_eks_cluster` — private endpoint enabled, public endpoint enabled with CIDR restriction via variable; logging: `["api","audit","authenticator","controllerManager","scheduler"]`; version via variable
- `aws_eks_node_group` — `t3.medium`, desired/min/max via variables, private subnets only, `AL2_x86_64` AMI, encrypted EBS root volume (20 GiB gp3)
- `aws_launch_template` — node group launch template with:
  - EBS encryption enabled
  - IMDSv2 required (`http_tokens = required`, `http_put_response_hop_limit = 1`)
  - No public IP
- `data.tls_certificate` — fetches OIDC thumbprint from EKS cluster's OIDC issuer URL
- `aws_iam_openid_connect_provider` — OIDC provider for IRSA; audience `sts.amazonaws.com`
- `aws_security_group.cluster` — EKS control plane SG
- `aws_security_group.nodes` — EKS node SG; allows all intra-node and node→control-plane traffic; no inbound from internet

Variables:
- `project` (string)
- `cluster_name` (string)
- `cluster_version` (string, default `"1.32"`)
- `cluster_role_arn` (string)
- `node_role_arn` (string)
- `vpc_id` (string)
- `private_subnet_ids` (list(string))
- `public_access_cidrs` (list(string), default `["0.0.0.0/0"]`) — restrict in prod to known CIDR
- `node_instance_type` (string, default `"t3.medium"`)
- `node_desired` (number, default `2`)
- `node_min` (number, default `2`)
- `node_max` (number, default `4`)
- `node_disk_size_gb` (number, default `20`)

Outputs:
- `cluster_name`
- `cluster_endpoint`
- `cluster_certificate_authority`
- `oidc_provider_arn`
- `oidc_provider_url`
- `node_group_name`

---

### modules/ecr/

**Purpose:** Container image repository with lifecycle management.

Resources:
- `aws_ecr_repository` — image tag immutability `IMMUTABLE`, image scanning on push enabled, KMS encryption (uses AWS-managed key `aws/ecr`)
- `aws_ecr_lifecycle_policy` — keep last 30 tagged images per semver tag pattern; expire untagged images after 7 days
- `aws_ecr_repository_policy` — allow node role to pull; allow deploy role to push

Variables:
- `project` (string)
- `repository_name` (string, default `"payment-processor"`)
- `node_role_arn` (string)
- `deploy_role_arn` (string)

Outputs:
- `repository_url`
- `repository_arn`
- `repository_name`

---

### environments/prod/

**Purpose:** Composes all modules for the single production environment.

Files:
- `backend.tf` — S3 backend with bucket, key `prod/terraform.tfstate`, region, DynamoDB lock table, encryption `true`
- `main.tf` — calls `vpc`, `iam`, `eks`, `ecr` modules with wired outputs
- `variables.tf` — declares all input variables with descriptions and types
- `outputs.tf` — re-exports key outputs: cluster endpoint, ECR URL, node group ARN, IRSA role ARN
- `terraform.tfvars` — sets concrete values for prod (project name, CIDRs, cluster version, node sizing, CI principal ARN)

Provider configuration:
- `aws` provider, `version ~> 5.0`, region from variable
- `tls` provider (for OIDC thumbprint)
- Required Terraform version `>= 1.9.0`

---

## Networking Model

```
us-east-1 VPC 10.0.0.0/16
├── Public Subnets (10.0.1-3.0/24) — one per AZ
│   ├── Internet Gateway (inbound + outbound internet)
│   ├── NAT Gateways (outbound only for private subnets)
│   └── Future: ALB (inbound to EKS)
└── Private Subnets (10.0.11-13.0/24) — one per AZ
    ├── EKS Managed Node Group (t3.medium)
    └── Future: RDS PostgreSQL
```

Nodes have no public IPs. All outbound traffic (ECR pulls, AWS API calls) routes through NAT Gateways. Inbound to nodes is only from within the VPC (ALB → nodes, control plane → nodes).

---

## IAM Relationships

```
EKS Control Plane
  └── eks_cluster_role  →  AmazonEKSClusterPolicy

EC2 Node Instances
  └── eks_node_role     →  AmazonEKSWorkerNodePolicy
                        →  AmazonEKS_CNI_Policy
                        →  AmazonEC2ContainerRegistryReadOnly

K8s Pod (payments-platform ServiceAccount)
  └── payments_app_role (IRSA via OIDC)
                        →  ECR pull (payments repo only)
                        →  Secrets Manager read (payments/* prefix only)

CI/CD Pipeline
  └── terraform_deploy_role (assumable by CI principal)
                        →  Least-privilege VPC/EKS/ECR/IAM/S3/DynamoDB
```

No wildcard `*` resource policies. All policies scope to ARNs or ARN prefixes of resources owned by this project.

---

## Terraform State

```
S3 bucket:        payments-platform-tf-state
  encryption:     SSE-S3 (AES-256)
  versioning:     enabled
  public access:  blocked (all four block settings)
  lifecycle:      state bucket is NOT managed by itself

DynamoDB table:   payments-platform-tf-lock
  hash key:       LockID (String)
  billing:        PAY_PER_REQUEST

Backend config (environments/prod/backend.tf):
  bucket         = "payments-platform-tf-state"
  key            = "prod/terraform.tfstate"
  region         = "us-east-1"
  dynamodb_table = "payments-platform-tf-lock"
  encrypt        = true
```

---

## Infrastructure Lifecycle

**Order of operations on first deploy:**
1. Apply `infra/bootstrap/` manually once to create S3 + DynamoDB
2. `cd infra/environments/prod && terraform init`
3. `terraform plan -out=tfplan`
4. `terraform apply tfplan`

**Dependency order within apply:**
VPC → IAM → EKS (consumes VPC + IAM outputs) → ECR (consumes IAM outputs)

**Ongoing changes:**
- Node AMI updates: bump `release_version` in node group config, `terraform apply`
- Cluster version upgrade: bump `cluster_version` variable, `terraform apply` (control plane first, then node group)
- Scaling: change `node_desired/min/max`, `terraform apply`
- New IAM policy additions: edit `modules/iam/`, `terraform apply`

**Destroy:**
`terraform destroy` removes all managed resources. S3 state bucket and DynamoDB table are in `bootstrap/` and are not destroyed by this. Manual cleanup of the bootstrap resources is a separate step documented in outputs.

---

## Security Notes

- IMDSv2 enforced on all nodes (hop limit 1, tokens required)
- EBS root volumes encrypted
- ECR images scanned on push
- ECR tags immutable (prevents tag overwrite)
- EKS control plane logs shipped to CloudWatch (all log types)
- No hardcoded secrets — all sensitive values via variables or IRSA
- `terraform.tfvars` must not contain secrets and must be committed; secrets injected via CI environment or AWS Secrets Manager
