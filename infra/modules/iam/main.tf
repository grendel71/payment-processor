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
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
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
