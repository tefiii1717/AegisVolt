# IRSA: cada ServiceAccount de Kubernetes asume su propio rol IAM.
# Database-per-Service reforzado por IAM: billing-wallet NO puede leer la tabla de reservas, etc.

locals {
  irsa_enabled = var.enable_eks && !var.use_floci && var.create_iam
}

data "aws_iam_policy_document" "irsa_trust" {
  for_each = local.irsa_enabled ? merge(local.service_tables, { "graphql-bff" = [], "prefect-flows" = [], "acl-ocpp-translator" = [] }) : {}
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks[0].oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks[0].oidc_provider}:sub"
      values   = ["system:serviceaccount:aegisvolt:${each.key}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks[0].oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  for_each           = data.aws_iam_policy_document.irsa_trust
  name               = "aegisvolt-${local.env}-${each.key}"
  assume_role_policy = each.value.json
}

data "aws_iam_policy_document" "service" {
  for_each = aws_iam_role.service

  # Tabla propia: lectura/escritura (incluye el GSI del outbox).
  dynamic "statement" {
    for_each = lookup(local.service_tables, each.key, [])
    content {
      sid = "OwnTable${replace(title(statement.value), "-", "")}"
      actions = [
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan",
        "dynamodb:TransactWriteItems", "dynamodb:DescribeTable", "dynamodb:ConditionCheckItem",
      ]
      resources = [module.dynamodb.table_arns[statement.value], "${module.dynamodb.table_arns[statement.value]}/index/*"]
    }
  }

  # Flags: solo lectura (las escribe exclusivamente la Lambda flags-admin).
  statement {
    sid       = "ReadFeatureFlags"
    actions   = ["dynamodb:Scan", "dynamodb:GetItem"]
    resources = [module.dynamodb.table_arns["flags"]]
  }

  # Prefect audita (solo lectura) retenciones y reservas, y publica métricas propias.
  dynamic "statement" {
    for_each = each.key == "prefect-flows" ? [1] : []
    content {
      sid       = "AuditSagaTables"
      actions   = ["dynamodb:Scan", "dynamodb:Query"]
      resources = [module.dynamodb.table_arns["billing"], module.dynamodb.table_arns["reservations"]]
    }
  }
  dynamic "statement" {
    for_each = each.key == "prefect-flows" ? [1] : []
    content {
      sid       = "PublishSagaMetrics"
      actions   = ["cloudwatch:PutMetricData"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "cloudwatch:namespace"
        values   = ["AegisVolt/Saga"]
      }
    }
  }

  # Sidecar Fluent Bit: escribir en /aegisvolt/<env>/*.
  statement {
    sid       = "SidecarLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aegisvolt/${local.env}/*"]
  }

  statement {
    sid       = "UseEnvironmentKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.env.arn]
  }
}

resource "aws_iam_role_policy" "service" {
  for_each = aws_iam_role.service
  role     = each.value.id
  policy   = data.aws_iam_policy_document.service[each.key].json
}

# Rol del AWS Load Balancer Controller (TargetGroupBinding hacia el NLB que crea Terraform).
module "lbc_irsa" {
  #checkov:skip=CKV_TF_1:Módulo del registro fijado por versión semántica (~> 5.48).
  source                                 = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version                                = "~> 6.11"
  count                                  = local.irsa_enabled ? 1 : 0
  role_name                              = "aegisvolt-${local.env}-aws-lb-controller"
  attach_load_balancer_controller_policy = true
  oidc_providers = {
    main = { provider_arn = module.eks[0].oidc_provider_arn, namespace_service_accounts = ["kube-system:aws-load-balancer-controller"] }
  }
}

# Rol de Argo Rollouts para leer métricas de CloudWatch durante el análisis del canary.
data "aws_iam_policy_document" "rollouts_trust" {
  count = local.irsa_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks[0].oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks[0].oidc_provider}:sub"
      values   = ["system:serviceaccount:argo-rollouts:argo-rollouts"]
    }
  }
}

resource "aws_iam_role" "rollouts" {
  count              = local.irsa_enabled ? 1 : 0
  name               = "aegisvolt-${local.env}-argo-rollouts"
  assume_role_policy = data.aws_iam_policy_document.rollouts_trust[0].json
}

resource "aws_iam_role_policy" "rollouts" {
  count = local.irsa_enabled ? 1 : 0
  role  = aws_iam_role.rollouts[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics"], Resource = "*"
  }] })
}
