# Borde: HTTP API pública → VPC Link v2 → NLB INTERNO → pods de graphql-bff.
#
# Recorrido de un paquete (pregunta 6.1):
#  1. Navegador → https://<api-id>.execute-api.<region>.amazonaws.com/graphql (TLS terminado en API Gateway).
#  2. El autorizador JWT valida firma/issuer/audiencia del access token de Cognito (401 si falla).
#  3. La integración HTTP_PROXY sale por la VPC Link: API Gateway crea ENIs en las subredes PRIVADAS
#     con el SG `vpclink` (egress solo al puerto del NLB).
#  4. El NLB interno (subredes privadas, SG propio) reenvía al target group tipo `ip`.
#  5. El AWS Load Balancer Controller registra en ese target group las IPs de los pods del BFF
#     (CRD TargetGroupBinding). El SG de nodos solo acepta el puerto 4000 desde el SG del NLB.
#  6. El pod BFF llama a los microservicios por Services ClusterIP (NetworkPolicies limitan quién habla con quién).

resource "aws_security_group" "vpclink" {
  #checkov:skip=CKV2_AWS_5:Sí está asociado: lo usa aws_apigatewayv2_vpc_link.this (Checkov no reconoce esa relación).
  name        = "aegisvolt-vpclink-${var.environment}"
  description = "ENIs de la VPC Link de API Gateway"
  vpc_id      = var.vpc_id
  egress {
    description = "Hacia el NLB interno"
    from_port   = var.bff_port
    to_port     = var.bff_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_security_group" "nlb" {
  name        = "aegisvolt-nlb-${var.environment}"
  description = "NLB interno del BFF"
  vpc_id      = var.vpc_id
  ingress {
    description     = "Solo desde la VPC Link"
    from_port       = var.bff_port
    to_port         = var.bff_port
    protocol        = "tcp"
    security_groups = [aws_security_group.vpclink.id]
  }
  egress {
    description = "Hacia los pods (target type ip)"
    from_port   = var.bff_port
    to_port     = var.bff_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_lb" "internal" {
  #checkov:skip=CKV_AWS_91:NLB TCP interno; el tráfico queda auditado por VPC Flow Logs y access logs de API Gateway.
  #checkov:skip=CKV2_AWS_20:No es un ALB: NLB TCP interno detrás de API Gateway, que ya termina TLS.
  name                             = "aegisvolt-bff-${var.environment}"
  internal                         = true
  load_balancer_type               = "network"
  subnets                          = var.private_subnet_ids
  security_groups                  = [aws_security_group.nlb.id]
  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = var.environment == "prod"
}

resource "aws_lb_target_group" "bff" {
  name        = "aegisvolt-bff-${var.environment}"
  port        = var.bff_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    protocol = "HTTP"
    path     = "/health/ready"
    port     = "traffic-port"
  }
}

resource "aws_lb_listener" "bff" {
  load_balancer_arn = aws_lb.internal.arn
  port              = var.bff_port
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bff.arn
  }
}

resource "aws_apigatewayv2_vpc_link" "this" {
  name               = "aegisvolt-${var.environment}"
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.vpclink.id]
}

resource "aws_apigatewayv2_api" "http" {
  name          = "aegisvolt-${var.environment}"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.cors_origins
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["authorization", "content-type", "x-correlation-id"]
    max_age       = 600
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = var.jwt_audiences
  }
}

resource "aws_apigatewayv2_integration" "bff" {
  api_id             = aws_apigatewayv2_api.http.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = aws_lb_listener.bff.arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.this.id
  request_parameters = { "overwrite:header.x-correlation-id" = "$context.requestId" }
}

resource "aws_apigatewayv2_route" "graphql" {
  api_id               = aws_apigatewayv2_api.http.id
  route_key            = "POST /graphql"
  target               = "integrations/${aws_apigatewayv2_integration.bff.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt.id
  authorization_scopes = var.required_scopes
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aegisvolt/${var.environment}/edge-api-access"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = var.throttle_burst
    throttling_rate_limit  = var.throttle_rate
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({ correlation_id = "$context.requestId", ip = "$context.identity.sourceIp", route = "$context.routeKey",
      status = "$context.status", latency_ms = "$context.integrationLatency", auth_error = "$context.authorizer.error",
    subject = "$context.authorizer.claims.sub" })
  }
}
