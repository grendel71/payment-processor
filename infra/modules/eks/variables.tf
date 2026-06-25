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
