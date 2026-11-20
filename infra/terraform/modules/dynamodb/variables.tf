variable "environment" { type = string }

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "point_in_time_recovery" {
  type    = bool
  default = true
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "flags" {
  description = "Estado inicial de las Feature Flags para este ambiente."
  type = map(object({
    enabled     = bool
    description = string
    prod_locked = bool
  }))
}
