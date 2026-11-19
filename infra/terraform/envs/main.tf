resource "aws_kms_key" "env" {
  description             = "AegisVolt ${local.env}: DynamoDB, CloudWatch Logs y secretos de EKS"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "env" {
  name          = "alias/aegisvolt-${local.env}"
  target_key_id = aws_kms_key.env.key_id
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_356:En una key policy, Resource "*" significa "esta llave"; es la forma obligatoria.
  #checkov:skip=CKV_AWS_111:Ídem: política de llave KMS, no política de identidad.
  #checkov:skip=CKV_AWS_109:Ídem: la cuenta raíz debe administrar la llave para evitar que quede huérfana.
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
  }
}

module "network" {
  source                = "../modules/network"
  name                  = "aegisvolt-${local.env}"
  environment           = local.env
  cidr                  = local.s.cidr
  az_count              = local.s.az_count
  nat_mode              = var.nat_mode
  log_retention_days    = local.s.log_retention
  kms_key_arn           = aws_kms_key.env.arn
  create_iam            = var.create_iam
  existing_iam_role_arn = var.existing_iam_role_arn
}

module "endpoints" {
  source                  = "../modules/vpc-endpoints"
  name                    = "aegisvolt-${local.env}"
  environment             = local.env
  vpc_id                  = module.network.vpc_id
  vpc_cidr                = module.network.vpc_cidr
  private_subnet_ids      = module.network.private_subnet_ids
  private_route_table_ids = module.network.private_route_table_ids
  interface_services = var.enable_interface_endpoints ? (
    var.enable_eks ? ["execute-api", "logs", "monitoring", "sts", "ecr.api", "ecr.dkr", "ec2", "eks", "eks-auth", "elasticloadbalancing"]
    : ["execute-api", "logs", "monitoring"]
  ) : []
}

module "dynamodb" {
  source                 = "../modules/dynamodb"
  environment            = local.env
  deletion_protection    = local.s.deletion_protection
  point_in_time_recovery = local.s.pitr
  kms_key_arn            = aws_kms_key.env.arn
  flags                  = local.flags
}

module "cognito" {
  source        = "../modules/cognito"
  environment   = local.env
  domain_suffix = var.cognito_domain_suffix
  callback_urls = [for o in var.web_origins : "${o}/"]
}

module "observability" {
  source             = "../modules/observability"
  environment        = local.env
  services           = local.services
  log_retention_days = local.s.log_retention
  kms_key_arn        = aws_kms_key.env.arn
}

module "flags_api" {
  source                  = "../modules/flags-api"
  count                   = var.enable_interface_endpoints ? 1 : 0
  environment             = local.env
  vpc_id                  = module.network.vpc_id
  vpc_cidr                = module.network.vpc_cidr
  dynamodb_prefix_list_id = module.endpoints.dynamodb_prefix_list_id
  private_subnet_ids      = module.network.private_subnet_ids
  execute_api_vpce_id     = module.endpoints.execute_api_endpoint_id
  flags_table_name        = module.dynamodb.table_names["flags"]
  flags_table_arn         = module.dynamodb.table_arns["flags"]
  handler_path            = "${path.module}/../../../serverless/flags-admin/handler.py"
  log_retention_days      = local.s.log_retention
  kms_key_arn             = aws_kms_key.env.arn
  create_iam              = var.create_iam
  existing_iam_role_arn   = var.existing_iam_role_arn
}

module "edge_api" {
  source             = "../modules/edge-api"
  environment        = local.env
  vpc_id             = module.network.vpc_id
  vpc_cidr           = module.network.vpc_cidr
  private_subnet_ids = module.network.private_subnet_ids
  jwt_issuer         = module.cognito.issuer
  jwt_audiences      = [module.cognito.web_client_id]
  cors_origins       = var.web_origins
  bff_port           = 80 # ingress-nginx dentro del clúster (enruta stable/canary del BFF)
  log_retention_days = local.s.log_retention
  kms_key_arn        = aws_kms_key.env.arn
}

module "eks" {
  source                = "../modules/eks"
  count                 = var.enable_eks && !var.use_floci ? 1 : 0
  environment           = local.env
  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  nlb_security_group_id = module.edge_api.nlb_security_group_id
  ingress_port          = 80
  public_access_cidrs   = var.eks_public_access_cidrs
  node_instance_types   = local.s.nodes.types
  node_capacity_type    = local.s.nodes.capacity
  node_min              = local.s.nodes.min
  node_max              = local.s.nodes.max
  node_desired          = local.s.nodes.desired
  create_iam            = var.create_iam
  existing_iam_role_arn = var.existing_iam_role_arn
}

module "eks_floci" {
  source             = "../modules/eks-floci"
  count              = var.enable_eks && var.use_floci ? 1 : 0
  environment        = local.env
  private_subnet_ids = module.network.private_subnet_ids
}
