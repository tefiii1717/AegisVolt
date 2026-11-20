output "log_group_names" {
  value = { for k, g in aws_cloudwatch_log_group.service : k => g.name }
}

output "log_group_arns" {
  value = { for k, g in aws_cloudwatch_log_group.service : k => g.arn }
}
