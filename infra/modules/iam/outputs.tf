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
