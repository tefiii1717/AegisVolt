locals {
  # El ambiente ES el workspace. Nunca se pasa como variable: así es imposible aplicar
  # la configuración de prod estando en el workspace de test (o viceversa).
  env = terraform.workspace

  settings = {
    dev = {
      cidr                = "10.10.0.0/16"
      az_count            = 2
      log_retention       = 30
      deletion_protection = false
      pitr                = false
      nodes = {
        types    = ["t3.large"]
        capacity = "SPOT"
        min      = 2
        max      = 3
        desired  = 2
      }
    }
    test = {
      cidr                = "10.20.0.0/16"
      az_count            = 2
      log_retention       = 30
      deletion_protection = false
      pitr                = true
      nodes = {
        types    = ["t3.large"]
        capacity = "SPOT"
        min      = 2
        max      = 3
        desired  = 2
      }
    }
    prod = {
      cidr                = "10.30.0.0/16"
      az_count            = 3
      log_retention       = 365
      deletion_protection = true
      pitr                = true
      nodes = {
        types    = ["t3.large"]
        capacity = "ON_DEMAND"
        min      = 3
        max      = 6
        desired  = 3
      }
    }
  }
  # try(): `terraform validate` corre en el workspace "default"; el plan/apply en un workspace
  # inválido lo detiene terraform_data.workspace_guard (precondición) más abajo.
  s = try(local.settings[local.env], local.settings.dev)

  # Estado inicial de las flags por ambiente, desde la MISMA fuente que usa el entorno local.
  catalog = jsondecode(file("${path.module}/../../../deploy/seed/catalog.json"))
  flags = { for name, f in local.catalog.flags : name => {
    enabled     = try(f[local.env], false)
    description = f.description
    prod_locked = f.prod_locked
  } }

  services = [
    "station-reservation-service", "billing-wallet-service", "energy-dispatch-service", "saga-orchestrator",
    "query-projection-service", "graphql-bff", "legacy-csms", "acl-ocpp-translator", "prefect-flows",
  ]

  # Qué tabla puede tocar cada microservicio (Database-per-Service reforzado con IAM).
  service_tables = {
    "station-reservation" = ["reservations"]
    "billing-wallet"      = ["billing"]
    "energy-dispatch"     = ["dispatch"]
    "saga-orchestrator"   = ["sagas"]
    "query-projection"    = ["readmodel"]
  }
}

resource "terraform_data" "workspace_guard" {
  lifecycle {
    precondition {
      condition     = contains(["dev", "test", "prod"], terraform.workspace)
      error_message = "Selecciona un workspace válido: terraform workspace select dev|test|prod (el workspace 'default' no se usa)."
    }
  }
}
