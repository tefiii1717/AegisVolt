output "environment" { value = local.env }
output "state_path" { value = "env:/${terraform.workspace}/aegisvolt/platform.tfstate" }
output "vpc_id" { value = module.network.vpc_id }
output "dynamodb_tables" { value = module.dynamodb.table_names }
output "graphql_endpoint" { value = "${module.edge_api.api_endpoint}/graphql" }
output "flags_api_private_url" { value = try(module.flags_api[0].invoke_url, null) }
output "cognito_issuer" { value = module.cognito.issuer }
output "cognito_web_client_id" { value = module.cognito.web_client_id }
output "cognito_hosted_ui" { value = module.cognito.hosted_ui_domain }
output "eks_cluster_name" { value = try(module.eks[0].cluster_name, module.eks_floci[0].cluster_name, null) }
output "log_groups" { value = module.observability.log_group_names }
output "service_role_arns" { value = { for k, r in aws_iam_role.service : k => r.arn } }
output "vpc_endpoints" {
  value = merge(module.endpoints.gateway_endpoint_ids, module.endpoints.interface_endpoint_ids)
}
output "bff_target_group_arn" { value = module.edge_api.bff_target_group_arn }
