# VPC Endpoints: el tráfico hacia servicios de AWS nunca sale a Internet.
#  * Gateway (S3, DynamoDB): se agregan como rutas (prefix list) en las tablas de rutas privadas. Gratis.
#  * Interface (PrivateLink): una ENI por subred privada con DNS privado; el SG solo permite 443 desde la VPC.
#    - execute-api : para consumir la REST API PRIVADA de API Gateway (flags-admin) desde la VPC.
#    - logs/monitoring: CloudWatch Logs y métricas (Fluent Bit, Prefect, Lambda).
#    - ecr.api/ecr.dkr/sts/ec2/eks/eks-auth/elasticloadbalancing: nodos EKS sin NAT.

data "aws_region" "current" {}

resource "aws_vpc_endpoint" "gateway" {
  for_each          = toset(["s3", "dynamodb"])
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_table_ids
  policy            = each.key == "dynamodb" ? data.aws_iam_policy_document.dynamodb_endpoint.json : null
  tags              = { Name = "${var.name}-vpce-${each.key}" }
}

# Política del endpoint: desde esta VPC solo se puede llegar a las tablas de ESTE ambiente.
data "aws_iam_policy_document" "dynamodb_endpoint" {
  statement {
    sid       = "OnlyThisEnvironmentTables"
    actions   = ["dynamodb:*"]
    resources = ["arn:aws:dynamodb:*:*:table/aegisvolt-*-${var.environment}", "arn:aws:dynamodb:*:*:table/aegisvolt-*-${var.environment}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-vpce"
  description = "HTTPS desde la VPC hacia Interface VPC Endpoints"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS desde la VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "Respuestas dentro de la VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(var.interface_services)
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${var.name}-vpce-${each.key}" }
}
