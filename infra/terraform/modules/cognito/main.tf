# OAuth2 con Amazon Cognito: user pool + resource server con scopes + clientes.
#  - web: cliente PÚBLICO (sin secreto) con Authorization Code + PKCE para la SPA.
#  - prefect: cliente CONFIDENCIAL con client_credentials (máquina a máquina) solo con scopes de lectura.

resource "aws_cognito_user_pool" "this" {
  name                     = "aegisvolt-${var.environment}"
  deletion_protection      = var.environment == "prod" ? "ACTIVE" : "INACTIVE"
  mfa_configuration        = var.environment == "prod" ? "OPTIONAL" : "OFF"
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  dynamic "software_token_mfa_configuration" {
    for_each = var.environment == "prod" ? [1] : []
    content { enabled = true }
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "aegisvolt-${var.environment}-${var.domain_suffix}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_resource_server" "api" {
  identifier   = "aegisvolt"
  name         = "AegisVolt API"
  user_pool_id = aws_cognito_user_pool.this.id

  dynamic "scope" {
    for_each = {
      "stations.read"      = "Consultar estaciones y conectores"
      "transactions.read"  = "Consultar las transacciones y facturas propias"
      "reservations.write" = "Iniciar reservas y cargas"
      "payments.manage"    = "Registrar medios de pago de la empresa"
      "audit.read"         = "Auditar todas las transacciones y facturas"
      "flags.admin"        = "Cambiar feature flags del ambiente"
      "users.admin"        = "Crear cuentas de operador, administrador y auditor"
      "platform.read"      = "Consultar la salud de la plataforma"
    }
    content {
      scope_name        = scope.key
      scope_description = scope.value
    }
  }
}

locals {
  all_scopes  = [for s in aws_cognito_resource_server.api.scope_identifiers : s]
  read_scopes = [for s in aws_cognito_resource_server.api.scope_identifiers : s if endswith(s, ".read")]
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "aegisvolt-web-${var.environment}"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = concat(["openid", "email"], local.all_scopes)
  callback_urls                        = var.callback_urls
  logout_urls                          = var.callback_urls
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_client" "prefect" {
  name                                 = "aegisvolt-prefect-${var.environment}"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = local.read_scopes
  supported_identity_providers         = ["COGNITO"]
}

resource "aws_cognito_user_group" "role" {
  for_each     = toset(["fleet-operator", "platform-admin", "auditor"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.this.id
}
