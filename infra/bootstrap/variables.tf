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
