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
