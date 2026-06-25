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
aws_account_id = "465457334498"

# Replace with the ARN of the IAM user/role used by your CI pipeline
ci_principal_arn = "arn:aws:iam::465457334498:user/blau700"
