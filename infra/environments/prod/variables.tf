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

variable "nat_gateway_count" {
  description = "Number of NAT Gateways. 1 reduces cost; 3 provides full AZ HA."
  type        = number
  default     = 1
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
  description = "ARN of the IAM user or role that may assume the Terraform deploy role from CI"
  type        = string
}
