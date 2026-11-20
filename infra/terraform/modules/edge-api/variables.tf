variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "jwt_issuer" { type = string }
variable "jwt_audiences" { type = list(string) }
variable "cors_origins" { type = list(string) }

variable "required_scopes" {
  description = "Scopes mínimos exigidos en el borde (el BFF aplica autorización fina por campo)."
  type        = list(string)
  default     = []
}

variable "bff_port" {
  type    = number
  default = 4000
}

variable "throttle_burst" {
  type    = number
  default = 100
}

variable "throttle_rate" {
  type    = number
  default = 50
}

variable "log_retention_days" {
  type    = number
  default = 365
}

variable "kms_key_arn" {
  type    = string
  default = null
}
