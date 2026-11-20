output "table_names" {
  value = merge({ for k, t in aws_dynamodb_table.service : k => t.name }, { flags = aws_dynamodb_table.flags.name })
}

output "table_arns" {
  value = merge({ for k, t in aws_dynamodb_table.service : k => t.arn }, { flags = aws_dynamodb_table.flags.arn })
}
