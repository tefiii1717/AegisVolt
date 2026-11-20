variable "environment" { type = string }

variable "services" {
  type = list(string)
}

variable "log_retention_days" {
  type    = number
  default = 365
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "error_alarm_threshold" {
  type    = number
  default = 5
}
