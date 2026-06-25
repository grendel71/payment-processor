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
