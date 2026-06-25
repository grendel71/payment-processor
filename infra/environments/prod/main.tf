module "vpc" {
  source = "../../modules/vpc"

  project              = var.project
  cluster_name         = var.cluster_name
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
}

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

module "ecr" {
  source = "../../modules/ecr"

  project         = var.project
  repository_name = var.ecr_repository_name
  node_role_arn   = module.iam.eks_node_role_arn
  deploy_role_arn = module.iam.terraform_deploy_role_arn
}

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
