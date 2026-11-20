output "api_endpoint" { value = aws_apigatewayv2_api.http.api_endpoint }
output "bff_target_group_arn" { value = aws_lb_target_group.bff.arn }
output "nlb_security_group_id" { value = aws_security_group.nlb.id }
output "nlb_dns_name" { value = aws_lb.internal.dns_name }
