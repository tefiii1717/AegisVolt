# Database-per-Service: una tabla por bounded context Y por ambiente.
#   aegisvolt-reservations-dev, aegisvolt-billing-test, aegisvolt-dispatch-prod, ...
# Como el nombre incluye terraform.workspace, un apply en `test` jamás puede tocar una tabla de `prod`.
# `identity` guarda los usuarios del proveedor de identidad local del BFF (en AWS real lo reemplaza Cognito).

locals {
  service_tables = toset(["reservations", "billing", "dispatch", "sagas", "readmodel", "identity"])
}

resource "aws_dynamodb_table" "service" {
  for_each                    = local.service_tables
  name                        = "aegisvolt-${each.key}-${var.environment}"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "gsi1pk"
    type = "S"
  }
  attribute {
    name = "gsi1sk"
    type = "S"
  }

  # GSI disperso: outbox pendiente, SAGAs activas (timeouts), sesiones activas, listado CQRS.
  global_secondary_index {
    name            = "gsi1"
    hash_key        = "gsi1pk"
    range_key       = "gsi1sk"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl" # expira registros de idempotencia y outbox ya publicados
    enabled        = true
  }

  point_in_time_recovery { enabled = var.point_in_time_recovery }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  tags = { BoundedContext = each.key }
}

resource "aws_dynamodb_table" "flags" {
  name                        = "aegisvolt-feature-flags-${var.environment}"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery { enabled = var.point_in_time_recovery }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

# Estado INICIAL de cada flag en este ambiente. ignore_changes = [item]: después de crearla, el
# valor lo gobierna la Lambda flags-admin; un `terraform apply` posterior NO revierte el cambio.
resource "aws_dynamodb_table_item" "flag" {
  for_each   = var.flags
  table_name = aws_dynamodb_table.flags.name
  hash_key   = aws_dynamodb_table.flags.hash_key
  range_key  = aws_dynamodb_table.flags.range_key
  item = jsonencode({
    pk          = { S = "FLAG#${each.key}" }
    sk          = { S = "FLAG" }
    enabled     = { BOOL = each.value.enabled }
    description = { S = each.value.description }
    prod_locked = { BOOL = each.value.prod_locked }
    updated_by  = { S = "terraform" }
  })
  lifecycle { ignore_changes = [item] }
}
