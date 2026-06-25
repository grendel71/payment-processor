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
