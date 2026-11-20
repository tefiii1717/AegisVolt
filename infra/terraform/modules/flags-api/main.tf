# Lambda flags-admin + REST API PRIVADA de API Gateway.
# Camino del paquete: pod graphql-bff → DNS privado de execute-api → ENI del Interface VPCE (SG 443)
#   → API Gateway (resource policy: solo aws:SourceVpce = este VPCE) → Lambda (en subredes privadas)
#   → DynamoDB por el Gateway VPCE (ruta en la tabla privada). Nada sale a Internet.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "archive_file" "lambda" {
  type        = "zip"
  source_file = var.handler_path
  output_path = "${path.module}/.build/flags-admin-${var.environment}.zip"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aegisvolt/${var.environment}/flags-admin"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  count              = var.create_iam ? 1 : 0
  name               = "aegisvolt-flags-admin-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

#tfsec:ignore:aws-iam-no-policy-wildcards Las acciones ec2:*NetworkInterface de Lambda en VPC no admiten recursos específicos.
resource "aws_iam_role_policy" "lambda" {
  count = var.create_iam ? 1 : 0
  role  = aws_iam_role.lambda[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"], Resource = var.flags_table_arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda.arn}:*" },
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.kms_key_arn != null ? var.kms_key_arn : "*" },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_security_group" "lambda" {
  name        = "aegisvolt-flags-admin-${var.environment}"
  description = "Lambda flags-admin: solo salida HTTPS dentro de la VPC (VPCE)"
  vpc_id      = var.vpc_id
  egress {
    description = "HTTPS hacia los Interface VPC Endpoints de la VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description     = "HTTPS hacia DynamoDB por el Gateway VPC Endpoint (prefix list)"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [var.dynamodb_prefix_list_id]
  }
}

resource "aws_lambda_function" "flags" {
  #checkov:skip=CKV_AWS_116:Invocación síncrona desde API Gateway; una DLQ solo aplica a invocaciones asíncronas.
  #checkov:skip=CKV_AWS_272:Firma de código fuera del alcance académico; el artefacto se construye en CI desde el repo.
  function_name                  = "aegisvolt-flags-admin-${var.environment}"
  role                           = var.create_iam ? aws_iam_role.lambda[0].arn : var.existing_iam_role_arn
  runtime                        = "python3.12"
  handler                        = "handler.lambda_handler"
  filename                       = data.archive_file.lambda.output_path
  source_code_hash               = data.archive_file.lambda.output_base64sha256
  timeout                        = 5
  memory_size                    = 256
  reserved_concurrent_executions = 10
  tracing_config { mode = "Active" }
  kms_key_arn = var.kms_key_arn
  environment {
    variables = { FLAGS_TABLE = var.flags_table_name, AEGIS_ENV = var.environment }
  }
  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }
  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ---------------- REST API privada ----------------
resource "aws_api_gateway_rest_api" "ops" {
  name = "aegisvolt-ops-${var.environment}"
  lifecycle { create_before_destroy = true }
  endpoint_configuration {
    types            = ["PRIVATE"]
    vpc_endpoint_ids = [var.execute_api_vpce_id]
  }
}

data "aws_iam_policy_document" "ops" {
  statement {
    effect    = "Allow"
    actions   = ["execute-api:Invoke"]
    resources = ["execute-api:/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
  statement {
    effect    = "Deny"
    actions   = ["execute-api:Invoke"]
    resources = ["execute-api:/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceVpce"
      values   = [var.execute_api_vpce_id]
    }
  }
}

resource "aws_api_gateway_rest_api_policy" "ops" {
  rest_api_id = aws_api_gateway_rest_api.ops.id
  policy      = data.aws_iam_policy_document.ops.json
}

resource "aws_api_gateway_resource" "flags" {
  rest_api_id = aws_api_gateway_rest_api.ops.id
  parent_id   = aws_api_gateway_rest_api.ops.root_resource_id
  path_part   = "flags"
}

resource "aws_api_gateway_resource" "flag" {
  rest_api_id = aws_api_gateway_rest_api.ops.id
  parent_id   = aws_api_gateway_resource.flags.id
  path_part   = "{name}"
}

locals {
  routes = {
    list = { resource = aws_api_gateway_resource.flags.id, method = "GET" }
    get  = { resource = aws_api_gateway_resource.flag.id, method = "GET" }
    put  = { resource = aws_api_gateway_resource.flag.id, method = "PUT" }
  }
}

resource "aws_api_gateway_request_validator" "ops" {
  name                        = "validar-parametros-y-cuerpo"
  rest_api_id                 = aws_api_gateway_rest_api.ops.id
  validate_request_body       = true
  validate_request_parameters = true
}

resource "aws_api_gateway_method" "route" {
  #checkov:skip=CKV_AWS_59:API PRIVADA: la resource policy niega todo lo que no venga del VPCE execute-api; la autorización por scope ocurre en el BFF.
  #checkov:skip=CKV2_AWS_53:Se valida con aws_api_gateway_request_validator.ops (falso positivo por for_each).
  for_each             = local.routes
  request_validator_id = aws_api_gateway_request_validator.ops.id
  rest_api_id          = aws_api_gateway_rest_api.ops.id
  resource_id          = each.value.resource
  http_method          = each.value.method
  authorization        = "NONE" # la autorización por scope ya la hizo el BFF; aquí manda la red (SourceVpce)
}

resource "aws_api_gateway_integration" "route" {
  for_each                = local.routes
  rest_api_id             = aws_api_gateway_rest_api.ops.id
  resource_id             = each.value.resource
  http_method             = aws_api_gateway_method.route[each.key].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.flags.invoke_arn
}

resource "aws_api_gateway_deployment" "ops" {
  rest_api_id = aws_api_gateway_rest_api.ops.id
  triggers = {
    redeploy = sha1(jsonencode([aws_api_gateway_integration.route, aws_api_gateway_rest_api_policy.ops.policy]))
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aegisvolt/${var.environment}/ops-api-access"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_api_gateway_stage" "ops" {
  #checkov:skip=CKV_AWS_120:Caché deshabilitada a propósito: un cambio de flag debe verse de inmediato (y la caché oculta canaries).
  #checkov:skip=CKV2_AWS_51:API privada alcanzable solo por PrivateLink; mTLS no aplica a endpoints PRIVATE.
  #checkov:skip=CKV2_AWS_4:El nivel de logging de ejecución requiere el rol de cuenta de API Gateway; se usan access logs JSON.
  rest_api_id          = aws_api_gateway_rest_api.ops.id
  deployment_id        = aws_api_gateway_deployment.ops.id
  stage_name           = var.environment
  xray_tracing_enabled = true
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      sourceVpce     = "$context.identity.vpceId"
      path           = "$context.path"
      method         = "$context.httpMethod"
      status         = "$context.status"
      correlation_id = "$context.requestId"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowOpsApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.flags.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.ops.execution_arn}/*/*"
}
