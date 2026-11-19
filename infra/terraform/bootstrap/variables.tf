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

variable "use_floci" {
  description = "true (por defecto) = bucket de estado y tabla de bloqueo en Floci."
  type        = bool
  default     = true
}

variable "floci_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

variable "create_ecr" {
  description = "Repositorios ECR compartidos (solo con AWS real; en Floci las imágenes se construyen localmente)."
  type        = bool
  default     = false
}

variable "create_github_oidc" {
  description = "Proveedor OIDC de GitHub y roles de despliegue (solo con AWS real)."
  type        = bool
  default     = false
}

variable "github_repository" {
  type    = string
  default = "tefiii1717/AegisVolt"
}
