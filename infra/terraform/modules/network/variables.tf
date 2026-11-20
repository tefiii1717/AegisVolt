variable "name" { type = string }
variable "environment" { type = string }

variable "cidr" {
  description = "CIDR de la VPC; distinto por ambiente para permitir peering futuro."
  type        = string
}

variable "az_count" {
  type    = number
  default = 2
}

variable "nat_mode" {
  description = "none = sin salida a Internet desde subredes privadas (solo VPCE); single = un NAT Gateway."
  type        = string
  default     = "none"
  validation {
    condition     = contains(["none", "single"], var.nat_mode)
    error_message = "nat_mode debe ser none o single."
  }
}

variable "log_retention_days" {
  type    = number
  default = 365
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "create_iam" {
  type    = bool
  default = true
}

variable "existing_iam_role_arn" {
  type    = string
  default = null
}
