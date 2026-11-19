output "execute_api_endpoint_id" {
  value = try(aws_vpc_endpoint.interface["execute-api"].id, null)
}

output "gateway_endpoint_ids" {
  value = { for k, v in aws_vpc_endpoint.gateway : k => v.id }
}

output "interface_endpoint_ids" {
  value = { for k, v in aws_vpc_endpoint.interface : k => v.id }
}

output "dynamodb_prefix_list_id" {
  value = aws_vpc_endpoint.gateway["dynamodb"].prefix_list_id
}

output "security_group_id" {
  value = aws_security_group.endpoints.id
}
