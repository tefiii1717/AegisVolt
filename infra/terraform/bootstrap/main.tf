# ---------------------------------------------------------------------------------------------
# Bootstrap (se aplica UNA vez, con estado local): recursos compartidos que no pertenecen a un
# ambiente y que deben existir antes que los workspaces dev/test/prod.
#   * Bucket S3 del estado remoto con VERSIONAMIENTO (backups históricos de cada tfstate).
#   * Repositorios ECR compartidos: la misma imagen (digest) se promueve dev → test → prod.
#   * Proveedor OIDC de GitHub + un rol de despliegue por ambiente (sin llaves de larga duración).
# ---------------------------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = "AegisVolt", ManagedBy = "Terraform", Stack = "bootstrap" } }
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name  = coalesce(var.state_bucket_name, "aegisvolt-tfstate-${data.aws_caller_identity.current.account_id}")
  environments = ["dev", "test", "prod"]
  services = ["station-reservation", "billing-wallet", "energy-dispatch", "saga-orchestrator", "query-projection",
  "legacy-csms", "flags-admin", "graphql-bff", "frontend", "acl-ocpp-translator", "prefect-flows"]
}

# ---------------- S3: estado remoto ----------------
#tfsec:ignore:aws-s3-enable-bucket-logging Auditoría por CloudTrail data events (ver comentario de Checkov).
resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_144:Los backups del estado se resuelven con versionamiento; la replicación entre regiones excede el alcance.
  #checkov:skip=CKV_AWS_18:Auditoría de acceso cubierta por CloudTrail data events; no se crea un segundo bucket de logs.
  #checkov:skip=CKV2_AWS_62:No hay consumidores de eventos del bucket de estado.
  bucket        = local.bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" } # cada apply deja una versión restaurable del tfstate
}

#tfsec:ignore:aws-s3-encryption-customer-key SSE-KMS con la llave administrada aws/s3; una CMK propia se puede agregar sin cambiar el diseño.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "retener-versiones-anteriores"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days           = var.state_version_retention_days
      newer_noncurrent_versions = 50
    }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json
}

# ---------------- ECR compartido ----------------
#tfsec:ignore:aws-ecr-repository-customer-key Cifrado KMS con la llave administrada de ECR.
resource "aws_ecr_repository" "service" {
  for_each             = toset(local.services)
  name                 = "aegisvolt/${each.key}"
  image_tag_mutability = "IMMUTABLE" # un tag nunca cambia de contenido → promoción reproducible
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "KMS" }
}

resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "Conservar las últimas 30 imágenes"
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
    action       = { type = "expire" }
  }] })
}

# ---------------- GitHub OIDC (sin access keys en el repositorio) ----------------
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_github_oidc ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_trust" {
  for_each = var.create_github_oidc ? toset(local.environments) : toset([])
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Solo jobs que corren en el GitHub Environment del mismo nombre pueden asumir el rol:
    # un workflow de un PR (o de otra rama) no puede desplegar a prod.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${each.key}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  for_each             = data.aws_iam_policy_document.github_trust
  name                 = "aegisvolt-deploy-${each.key}"
  assume_role_policy   = each.value.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "deploy_admin" {
  #checkov:skip=CKV_AWS_274:Riesgo aceptado y documentado (docs/01): el rol solo lo asume el GitHub Environment del mismo ambiente.
  # Simplificación académica: en un entorno real se usaría una política acotada por ambiente.
  for_each   = aws_iam_role.deploy
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# Rol de SOLO LECTURA para `terraform plan` en Pull Requests (no requiere GitHub Environment).
data "aws_iam_policy_document" "github_plan_trust" {
  count = var.create_github_oidc ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  count              = var.create_github_oidc ? 1 : 0
  name               = "aegisvolt-plan"
  assume_role_policy = data.aws_iam_policy_document.github_plan_trust[0].json
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  count      = var.create_github_oidc ? 1 : 0
  role       = aws_iam_role.plan[0].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

#tfsec:ignore:aws-iam-no-policy-wildcards Los archivos .tflock existen por workspace (env:/<ws>/...); el comodín está acotado a *.tflock del bucket de estado.
resource "aws_iam_role_policy" "plan_state_lock" {
  count = var.create_github_oidc ? 1 : 0
  role  = aws_iam_role.plan[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow"
    Action   = ["s3:PutObject", "s3:DeleteObject"]
    Resource = "${aws_s3_bucket.state.arn}/*.tflock"
  }] })
}
