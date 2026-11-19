output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = try(aws_dynamodb_table.tflock[0].name, null)
}

output "ecr_registry" {
  value = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

output "deploy_role_arns" {
  value = { for env, role in aws_iam_role.deploy : env => role.arn }
}

output "plan_role_arn" {
  value = try(aws_iam_role.plan[0].arn, null)
}
