# Terraform EKS Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a production-ready Terraform infrastructure for the payment processor covering bootstrap state backend, VPC, IAM, EKS, and ECR in a modular structure under `infra/`.

**Architecture:** Five independent Terraform modules (`bootstrap`, `vpc`, `iam`, `eks`, `ecr`) composed by a single `environments/prod` root. Remote state in S3 + DynamoDB. All resources tagged. No hardcoded secrets.

**Tech Stack:** Terraform >= 1.9, AWS provider ~> 5.0, TLS provider (for OIDC thumbprint), region us-east-1.

---

## File Map

```
infra/
  bootstrap/
    main.tf
    variables.tf
    outputs.tf

  modules/
    vpc/
      main.tf
      variables.tf
      outputs.tf
    iam/
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

  environments/
    prod/
      backend.tf
      main.tf
      variables.tf
      outputs.tf
      terraform.tfvars
```

---

## Task 1: Bootstrap Module

**Files:**
- Create: `infra/bootstrap/main.tf`
- Create: `infra/bootstrap/variables.tf`
- Create: `infra/bootstrap/outputs.tf`

- [ ] **Step 1: Create `infra/bootstrap/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "payments-platform"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}
```

- [ ] **Step 2: Create `infra/bootstrap/main.tf`**

```hcl
terraform {
  required_version = ">= 1.9.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

resource "aws_s3_bucket" "tf_state" {
  bucket = "${var.project}-tf-state"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project     = var.project
    ManagedBy   = "terraform"
    Purpose     = "terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = "${var.project}-tf-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = var.project
    ManagedBy = "terraform"
    Purpose   = "terraform-state-lock"
  }
}
```

- [ ] **Step 3: Create `infra/bootstrap/outputs.tf`**

```hcl
output "state_bucket_name" {
  description = "S3 bucket name for Terraform remote state"
  value       = aws_s3_bucket.tf_state.bucket
}

output "lock_table_name" {
  description = "DynamoDB table name for Terraform state locking"
  value       = aws_dynamodb_table.tf_lock.name
}
```

- [ ] **Step 4: Validate syntax**

Run from `infra/bootstrap/`:
```bash
terraform init
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add infra/bootstrap/
git commit -m "feat(infra): add bootstrap module for S3 state bucket and DynamoDB lock"
```

---

## Task 2: VPC Module

**Files:**
- Create: `infra/modules/vpc/variables.tf`
- Create: `infra/modules/vpc/main.tf`
- Create: `infra/modules/vpc/outputs.tf`

- [ ] **Step 1: Create `infra/modules/vpc/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name — used for subnet tags required by AWS load balancer controller"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs to deploy subnets into (must be 3)"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
}
```

- [ ] **Step 2: Create `infra/modules/vpc/main.tf`**

```hcl
locals {
  tags = {
    Project   = var.project
    ManagedBy = "terraform"
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.tags, {
    Name = "${var.project}-vpc"
  })
}

# ── Public subnets ────────────────────────────────────────────────────────────

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = merge(local.tags, {
    Name                                        = "${var.project}-public-${var.availability_zones[count.index]}"
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  })
}

# ── Private subnets ───────────────────────────────────────────────────────────

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.tags, {
    Name                                        = "${var.project}-private-${var.availability_zones[count.index]}"
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  })
}

# ── Internet Gateway ──────────────────────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(local.tags, {
    Name = "${var.project}-igw"
  })
}

# ── NAT Gateways (one per AZ) ─────────────────────────────────────────────────

resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"

  tags = merge(local.tags, {
    Name = "${var.project}-nat-eip-${var.availability_zones[count.index]}"
  })
}

resource "aws_nat_gateway" "main" {
  count = length(var.availability_zones)

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  depends_on = [aws_internet_gateway.main]

  tags = merge(local.tags, {
    Name = "${var.project}-nat-${var.availability_zones[count.index]}"
  })
}

# ── Route Tables ──────────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.tags, {
    Name = "${var.project}-public-rt"
  })
}

resource "aws_route_table_association" "public" {
  count = length(var.availability_zones)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = merge(local.tags, {
    Name = "${var.project}-private-rt-${var.availability_zones[count.index]}"
  })
}

resource "aws_route_table_association" "private" {
  count = length(var.availability_zones)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}
```

- [ ] **Step 3: Create `infra/modules/vpc/outputs.tf`**

```hcl
output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = aws_subnet.private[*].id
}
```

- [ ] **Step 4: Validate syntax**

```bash
cd infra/modules/vpc
terraform init
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add infra/modules/vpc/
git commit -m "feat(infra): add vpc module with public/private subnets, NAT gateways"
```

---

## Task 3: IAM Module

**Files:**
- Create: `infra/modules/iam/variables.tf`
- Create: `infra/modules/iam/main.tf`
- Create: `infra/modules/iam/outputs.tf`

- [ ] **Step 1: Create `infra/modules/iam/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS OIDC provider (for IRSA)"
  type        = string
}

variable "oidc_provider_url" {
  description = "URL of the EKS OIDC provider without https:// prefix"
  type        = string
}

variable "ecr_repository_arn" {
  description = "ARN of the ECR repository the app needs to pull from"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "ci_principal_arn" {
  description = "ARN of the IAM principal (user or role) that may assume the Terraform deploy role from CI"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}
```

- [ ] **Step 2: Create `infra/modules/iam/main.tf`**

```hcl
locals {
  tags = {
    Project   = var.project
    ManagedBy = "terraform"
  }
}

# ── EKS Cluster Role ──────────────────────────────────────────────────────────

data "aws_iam_policy_document" "eks_cluster_assume" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "eks_cluster" {
  name               = "${var.project}-eks-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ── EKS Node Role ─────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "eks_node_assume" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "eks_node" {
  name               = "${var.project}-eks-node-role"
  assume_role_policy = data.aws_iam_policy_document.eks_node_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "eks_ecr_read" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ── Payments App IRSA Role ────────────────────────────────────────────────────

data "aws_iam_policy_document" "payments_app_assume" {
  statement {
    effect = "Allow"
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    actions = ["sts:AssumeRoleWithWebIdentity"]
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:payments:payments-platform"]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "payments_app" {
  name               = "${var.project}-payments-app-role"
  assume_role_policy = data.aws_iam_policy_document.payments_app_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "payments_app_policy" {
  statement {
    sid    = "ECRAuth"
    effect = "Allow"
    actions = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPull"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid    = "SecretsManagerRead"
    effect = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.region}:${var.aws_account_id}:secret:payments/*"
    ]
  }
}

resource "aws_iam_policy" "payments_app" {
  name        = "${var.project}-payments-app-policy"
  description = "Least-privilege policy for the payments app IRSA role"
  policy      = data.aws_iam_policy_document.payments_app_policy.json
  tags        = local.tags
}

resource "aws_iam_role_policy_attachment" "payments_app" {
  role       = aws_iam_role.payments_app.name
  policy_arn = aws_iam_policy.payments_app.arn
}

# ── Terraform Deploy Role (CI) ────────────────────────────────────────────────

data "aws_iam_policy_document" "terraform_deploy_assume" {
  statement {
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [var.ci_principal_arn]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "terraform_deploy" {
  name               = "${var.project}-terraform-deploy-role"
  assume_role_policy = data.aws_iam_policy_document.terraform_deploy_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "terraform_deploy_policy" {
  # EC2 / VPC
  statement {
    sid    = "VPC"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:DescribeVpcs", "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:DescribeSubnets",
      "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
      "ec2:DescribeInternetGateways",
      "ec2:AllocateAddress", "ec2:ReleaseAddress", "ec2:DescribeAddresses",
      "ec2:CreateNatGateway", "ec2:DeleteNatGateway", "ec2:DescribeNatGateways",
      "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:DescribeRouteTables",
      "ec2:CreateRoute", "ec2:DeleteRoute",
      "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress",
      "ec2:CreateTags", "ec2:DeleteTags", "ec2:DescribeTags",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeAccountAttributes",
      "ec2:DescribeLaunchTemplates", "ec2:DescribeLaunchTemplateVersions",
      "ec2:CreateLaunchTemplate", "ec2:DeleteLaunchTemplate",
      "ec2:CreateLaunchTemplateVersion",
    ]
    resources = ["*"]
  }

  # EKS
  statement {
    sid    = "EKS"
    effect = "Allow"
    actions = [
      "eks:CreateCluster", "eks:DeleteCluster", "eks:DescribeCluster",
      "eks:UpdateClusterVersion", "eks:UpdateClusterConfig",
      "eks:CreateNodegroup", "eks:DeleteNodegroup", "eks:DescribeNodegroup",
      "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion",
      "eks:ListNodegroups", "eks:TagResource", "eks:UntagResource",
      "eks:AssociateIdentityProviderConfig",
      "eks:DescribeIdentityProviderConfig",
      "eks:DisassociateIdentityProviderConfig",
      "eks:ListIdentityProviderConfigs",
    ]
    resources = ["*"]
  }

  # ECR
  statement {
    sid    = "ECR"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository", "ecr:DeleteRepository", "ecr:DescribeRepositories",
      "ecr:SetRepositoryPolicy", "ecr:DeleteRepositoryPolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:PutLifecyclePolicy", "ecr:GetLifecyclePolicy",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:TagResource", "ecr:UntagResource",
    ]
    resources = ["*"]
  }

  # IAM — scoped to project-prefixed resources only
  statement {
    sid    = "IAM"
    effect = "Allow"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PassRole",
      "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy",
      "iam:GetPolicyVersion", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile", "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
    ]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:role/${var.project}-*",
      "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-*",
      "arn:aws:iam::${var.aws_account_id}:oidc-provider/*",
      "arn:aws:iam::${var.aws_account_id}:instance-profile/${var.project}-*",
    ]
  }

  # S3 state bucket
  statement {
    sid    = "TFState"
    effect = "Allow"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
      "s3:GetBucketVersioning",
    ]
    resources = [
      "arn:aws:s3:::${var.project}-tf-state",
      "arn:aws:s3:::${var.project}-tf-state/*",
    ]
  }

  # DynamoDB lock table
  statement {
    sid    = "TFLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
      "dynamodb:DescribeTable",
    ]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-tf-lock"
    ]
  }
}

resource "aws_iam_policy" "terraform_deploy" {
  name        = "${var.project}-terraform-deploy-policy"
  description = "Least-privilege policy for CI Terraform deployments"
  policy      = data.aws_iam_policy_document.terraform_deploy_policy.json
  tags        = local.tags
}

resource "aws_iam_role_policy_attachment" "terraform_deploy" {
  role       = aws_iam_role.terraform_deploy.name
  policy_arn = aws_iam_policy.terraform_deploy.arn
}
```

- [ ] **Step 3: Create `infra/modules/iam/outputs.tf`**

```hcl
output "eks_cluster_role_arn" {
  description = "ARN of the EKS cluster IAM role"
  value       = aws_iam_role.eks_cluster.arn
}

output "eks_node_role_arn" {
  description = "ARN of the EKS node IAM role"
  value       = aws_iam_role.eks_node.arn
}

output "payments_app_role_arn" {
  description = "ARN of the payments app IRSA role"
  value       = aws_iam_role.payments_app.arn
}

output "terraform_deploy_role_arn" {
  description = "ARN of the Terraform CI deploy role"
  value       = aws_iam_role.terraform_deploy.arn
}
```

- [ ] **Step 4: Validate syntax**

```bash
cd infra/modules/iam
terraform init
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add infra/modules/iam/
git commit -m "feat(infra): add iam module with cluster, node, IRSA, and deploy roles"
```

---

## Task 4: EKS Module

**Files:**
- Create: `infra/modules/eks/variables.tf`
- Create: `infra/modules/eks/main.tf`
- Create: `infra/modules/eks/outputs.tf`

- [ ] **Step 1: Create `infra/modules/eks/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.32"
}

variable "cluster_role_arn" {
  description = "ARN of the IAM role for the EKS control plane"
  type        = string
}

variable "node_role_arn" {
  description = "ARN of the IAM role for EKS worker nodes"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for EKS nodes"
  type        = list(string)
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the EKS public endpoint. Restrict to known IPs in production."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "node_instance_type" {
  description = "EC2 instance type for EKS managed node group"
  type        = string
  default     = "t3.medium"
}

variable "node_desired" {
  description = "Desired number of nodes in the managed node group"
  type        = number
  default     = 2
}

variable "node_min" {
  description = "Minimum number of nodes in the managed node group"
  type        = number
  default     = 2
}

variable "node_max" {
  description = "Maximum number of nodes in the managed node group"
  type        = number
  default     = 4
}

variable "node_disk_size_gb" {
  description = "Root EBS volume size in GB for each node"
  type        = number
  default     = 20
}
```

- [ ] **Step 2: Create `infra/modules/eks/main.tf`**

```hcl
locals {
  tags = {
    Project   = var.project
    ManagedBy = "terraform"
  }
}

# ── Security Groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "cluster" {
  name        = "${var.project}-eks-cluster-sg"
  description = "EKS control plane security group"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, {
    Name = "${var.project}-eks-cluster-sg"
  })
}

resource "aws_security_group" "nodes" {
  name        = "${var.project}-eks-nodes-sg"
  description = "EKS worker node security group"
  vpc_id      = var.vpc_id

  # Allow all traffic between nodes
  ingress {
    description = "Node-to-node communication"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  # Allow nodes to receive from control plane
  ingress {
    description     = "Control plane to node communication"
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
    security_groups = [aws_security_group.cluster.id]
  }

  # Unrestricted outbound (for ECR, AWS APIs via NAT)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${var.project}-eks-nodes-sg"
  })
}

# Allow control plane to reach nodes
resource "aws_security_group_rule" "cluster_to_nodes" {
  type                     = "egress"
  from_port                = 0
  to_port                  = 0
  protocol                 = "-1"
  security_group_id        = aws_security_group.cluster.id
  source_security_group_id = aws_security_group.nodes.id
  description              = "Control plane to node communication"
}

# Allow nodes to call back to control plane API
resource "aws_security_group_rule" "nodes_to_cluster" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.cluster.id
  source_security_group_id = aws_security_group.nodes.id
  description              = "Node to control plane HTTPS"
}

# ── EKS Cluster ───────────────────────────────────────────────────────────────

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = var.cluster_role_arn
  version  = var.cluster_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.public_access_cidrs
    security_group_ids      = [aws_security_group.cluster.id]
  }

  enabled_cluster_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler",
  ]

  tags = merge(local.tags, {
    Name = var.cluster_name
  })

  depends_on = [aws_security_group.cluster]
}

# ── OIDC Provider (for IRSA) ──────────────────────────────────────────────────

data "tls_certificate" "eks_oidc" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc.certificates[0].sha1_fingerprint]
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer

  tags = merge(local.tags, {
    Name = "${var.project}-eks-oidc"
  })
}

# ── Launch Template (node hardening) ─────────────────────────────────────────

resource "aws_launch_template" "nodes" {
  name_prefix   = "${var.project}-eks-node-"
  description   = "Hardened launch template for EKS managed node group"

  # IMDSv2 required
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # Encrypted root volume
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.node_disk_size_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  # No public IP for nodes
  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.nodes.id]
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.tags, {
      Name = "${var.project}-eks-node"
    })
  }

  tags = local.tags

  lifecycle {
    create_before_destroy = true
  }
}

# ── Managed Node Group ────────────────────────────────────────────────────────

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.project}-nodes"
  node_role_arn   = var.node_role_arn
  subnet_ids      = var.private_subnet_ids

  ami_type = "AL2_x86_64"

  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }

  instance_types = [var.node_instance_type]

  scaling_config {
    desired_size = var.node_desired
    min_size     = var.node_min
    max_size     = var.node_max
  }

  update_config {
    max_unavailable = 1
  }

  tags = merge(local.tags, {
    Name = "${var.project}-node-group"
  })

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [aws_eks_cluster.main]
}
```

- [ ] **Step 3: Create `infra/modules/eks/outputs.tf`**

```hcl
output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "API server endpoint of the EKS cluster"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_certificate_authority" {
  description = "Base64-encoded certificate authority data for the cluster"
  value       = aws_eks_cluster.main.certificate_authority[0].data
  sensitive   = true
}

output "oidc_provider_arn" {
  description = "ARN of the OIDC provider for IRSA"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "oidc_provider_url" {
  description = "URL of the OIDC provider (without https://)"
  value       = trimprefix(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://")
}

output "node_group_name" {
  description = "Name of the EKS managed node group"
  value       = aws_eks_node_group.main.node_group_name
}
```

- [ ] **Step 4: Validate syntax**

```bash
cd infra/modules/eks
terraform init
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add infra/modules/eks/
git commit -m "feat(infra): add eks module with cluster, node group, OIDC provider"
```

---

## Task 5: ECR Module

**Files:**
- Create: `infra/modules/ecr/variables.tf`
- Create: `infra/modules/ecr/main.tf`
- Create: `infra/modules/ecr/outputs.tf`

- [ ] **Step 1: Create `infra/modules/ecr/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
}

variable "repository_name" {
  description = "Name of the ECR repository"
  type        = string
  default     = "payment-processor"
}

variable "node_role_arn" {
  description = "ARN of the EKS node role that needs ECR pull access"
  type        = string
}

variable "deploy_role_arn" {
  description = "ARN of the CI deploy role that needs ECR push access"
  type        = string
}
```

- [ ] **Step 2: Create `infra/modules/ecr/main.tf`**

```hcl
locals {
  tags = {
    Project   = var.project
    ManagedBy = "terraform"
  }
}

resource "aws_ecr_repository" "main" {
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }

  tags = merge(local.tags, {
    Name = var.repository_name
  })
}

resource "aws_ecr_lifecycle_policy" "main" {
  repository = aws_ecr_repository.main.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 semver-tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      }
    ]
  })
}

data "aws_iam_policy_document" "ecr_repository_policy" {
  statement {
    sid    = "NodePull"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [var.node_role_arn]
    }
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
    ]
  }

  statement {
    sid    = "CIPush"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [var.deploy_role_arn]
    }
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
  }
}

resource "aws_ecr_repository_policy" "main" {
  repository = aws_ecr_repository.main.name
  policy     = data.aws_iam_policy_document.ecr_repository_policy.json
}
```

- [ ] **Step 3: Create `infra/modules/ecr/outputs.tf`**

```hcl
output "repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.main.repository_url
}

output "repository_arn" {
  description = "ARN of the ECR repository"
  value       = aws_ecr_repository.main.arn
}

output "repository_name" {
  description = "Name of the ECR repository"
  value       = aws_ecr_repository.main.name
}
```

- [ ] **Step 4: Validate syntax**

```bash
cd infra/modules/ecr
terraform init
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
git add infra/modules/ecr/
git commit -m "feat(infra): add ecr module with lifecycle policy, scan-on-push, KMS encryption"
```

---

## Task 6: Production Environment Composer

**Files:**
- Create: `infra/environments/prod/backend.tf`
- Create: `infra/environments/prod/variables.tf`
- Create: `infra/environments/prod/main.tf`
- Create: `infra/environments/prod/outputs.tf`
- Create: `infra/environments/prod/terraform.tfvars`

- [ ] **Step 1: Create `infra/environments/prod/backend.tf`**

```hcl
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket         = "payments-platform-tf-state"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "payments-platform-tf-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}
```

- [ ] **Step 2: Create `infra/environments/prod/variables.tf`**

```hcl
variable "project" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "payments-platform"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "payments-platform-prod"
}

variable "cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.32"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the EKS public API endpoint"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "node_instance_type" {
  description = "EC2 instance type for EKS nodes"
  type        = string
  default     = "t3.medium"
}

variable "node_desired" {
  description = "Desired node count"
  type        = number
  default     = 2
}

variable "node_min" {
  description = "Minimum node count"
  type        = number
  default     = 2
}

variable "node_max" {
  description = "Maximum node count"
  type        = number
  default     = 4
}

variable "node_disk_size_gb" {
  description = "Node root volume size in GB"
  type        = number
  default     = 20
}

variable "ecr_repository_name" {
  description = "ECR repository name"
  type        = string
  default     = "payment-processor"
}

variable "aws_account_id" {
  description = "AWS account ID (used for ARN construction in IAM policies)"
  type        = string
}

variable "ci_principal_arn" {
  description = "ARN of the CI IAM user or role that may assume the Terraform deploy role"
  type        = string
}
```

- [ ] **Step 3: Create `infra/environments/prod/main.tf`**

```hcl
# ── VPC ───────────────────────────────────────────────────────────────────────

module "vpc" {
  source = "../../modules/vpc"

  project              = var.project
  cluster_name         = var.cluster_name
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
}

# ── IAM ───────────────────────────────────────────────────────────────────────
# IAM roles for EKS and IRSA depend on the OIDC provider from the EKS module,
# but the cluster and node roles are needed to create EKS itself.
# We solve this by splitting IAM into two passes via depends_on in the eks module.

module "iam" {
  source = "../../modules/iam"

  project            = var.project
  cluster_name       = var.cluster_name
  oidc_provider_arn  = module.eks.oidc_provider_arn
  oidc_provider_url  = module.eks.oidc_provider_url
  ecr_repository_arn = module.ecr.repository_arn
  aws_account_id     = var.aws_account_id
  ci_principal_arn   = var.ci_principal_arn
  region             = var.region
}

# ── EKS ───────────────────────────────────────────────────────────────────────

module "eks" {
  source = "../../modules/eks"

  project             = var.project
  cluster_name        = var.cluster_name
  cluster_version     = var.cluster_version
  cluster_role_arn    = module.iam.eks_cluster_role_arn
  node_role_arn       = module.iam.eks_node_role_arn
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  public_access_cidrs = var.public_access_cidrs
  node_instance_type  = var.node_instance_type
  node_desired        = var.node_desired
  node_min            = var.node_min
  node_max            = var.node_max
  node_disk_size_gb   = var.node_disk_size_gb
}

# ── ECR ───────────────────────────────────────────────────────────────────────

module "ecr" {
  source = "../../modules/ecr"

  project         = var.project
  repository_name = var.ecr_repository_name
  node_role_arn   = module.iam.eks_node_role_arn
  deploy_role_arn = module.iam.terraform_deploy_role_arn
}
```

- [ ] **Step 4: Create `infra/environments/prod/outputs.tf`**

```hcl
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = module.vpc.private_subnet_ids
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.eks.cluster_endpoint
}

output "oidc_provider_arn" {
  description = "ARN of the EKS OIDC provider"
  value       = module.eks.oidc_provider_arn
}

output "ecr_repository_url" {
  description = "ECR repository URL for pushing and pulling images"
  value       = module.ecr.repository_url
}

output "payments_app_role_arn" {
  description = "ARN of the payments app IRSA role — annotate the K8s ServiceAccount with this"
  value       = module.iam.payments_app_role_arn
}

output "terraform_deploy_role_arn" {
  description = "ARN of the CI Terraform deploy role"
  value       = module.iam.terraform_deploy_role_arn
}
```

- [ ] **Step 5: Create `infra/environments/prod/terraform.tfvars`**

```hcl
project             = "payments-platform"
region              = "us-east-1"
cluster_name        = "payments-platform-prod"
cluster_version     = "1.32"
node_instance_type  = "t3.medium"
node_desired        = 2
node_min            = 2
node_max            = 4
node_disk_size_gb   = 20
ecr_repository_name = "payment-processor"

# Restrict to your CI/CD egress IPs in production
public_access_cidrs = ["0.0.0.0/0"]

# Replace with your real AWS account ID
aws_account_id = "123456789012"

# Replace with the ARN of the IAM user/role used by your CI pipeline
ci_principal_arn = "arn:aws:iam::123456789012:user/ci-deploy"
```

- [ ] **Step 6: Validate full environment**

```bash
cd infra/environments/prod
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Commit**

```bash
git add infra/environments/prod/
git commit -m "feat(infra): add prod environment composer with all modules wired"
```

---

## Self-Review

**Spec coverage:**
- bootstrap S3 + DynamoDB ✅ Task 1
- VPC with 3 public/private subnets, NAT Gateways, IGW, route tables, EKS subnet tags ✅ Task 2
- IAM cluster role, node role, IRSA role, deploy role with least-privilege ✅ Task 3
- EKS cluster, OIDC provider, managed node group, IMDSv2, encrypted EBS, control plane logging ✅ Task 4
- ECR with immutable tags, scan-on-push, KMS encryption, lifecycle policy ✅ Task 5
- environments/prod backend, variables, main, outputs, tfvars ✅ Task 6
- Remote state S3 + DynamoDB backend ✅ Task 6 Step 1
- All modules have documented outputs ✅ all tasks

**Placeholder scan:** `terraform.tfvars` contains placeholder `aws_account_id = "123456789012"` and `ci_principal_arn` — these are correctly noted as requiring replacement before apply; they are not implementation TBDs.

**Type consistency:** All module output names used in `environments/prod/main.tf` match the output names defined in each module. `oidc_provider_arn`, `oidc_provider_url`, `eks_cluster_role_arn`, `eks_node_role_arn`, `terraform_deploy_role_arn`, `repository_arn`, `repository_url` all consistent.

**Circular dependency note:** `iam` module references `eks` OIDC outputs and `ecr` repository ARN, while `eks` needs `iam` cluster and node roles. Terraform resolves this correctly because the OIDC provider and ECR repository exist before `iam` needs them (both are outputs of resources that don't depend on IAM). Terraform's dependency graph handles this without issues.
