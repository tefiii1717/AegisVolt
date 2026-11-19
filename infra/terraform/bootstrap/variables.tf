variable "region" {
  type    = string
  default = "us-east-1"
}

variable "state_bucket_name" {
  description = "Nombre del bucket de estado (por defecto aegisvolt-tfstate-<account_id>)."
  type        = string
  default     = null
}

variable "state_version_retention_days" {
  description = "Días que se conservan las versiones anteriores del tfstate."
  type        = number
  default     = 90
}

variable "create_github_oidc" {
  description = "Crear proveedor OIDC de GitHub y roles de despliegue (false en cuentas sin permisos IAM, p. ej. Learner Lab)."
  type        = bool
  default     = true
}

variable "github_repository" {
  type    = string
  default = "tefiii1717/AegisVolt"
}
