output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "issuer" { value = "https://${aws_cognito_user_pool.this.endpoint}" }
output "web_client_id" { value = aws_cognito_user_pool_client.web.id }
output "prefect_client_id" { value = aws_cognito_user_pool_client.prefect.id }
output "hosted_ui_domain" { value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.name}.amazoncognito.com" }

data "aws_region" "current" {}
