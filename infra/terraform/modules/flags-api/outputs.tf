output "invoke_url" {
  description = "URL privada (solo resoluble/alcanzable dentro de la VPC vía el VPCE execute-api)."
  value       = "https://${aws_api_gateway_rest_api.ops.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/${aws_api_gateway_stage.ops.stage_name}"
}

output "lambda_name" { value = aws_lambda_function.flags.function_name }
