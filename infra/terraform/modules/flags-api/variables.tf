variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "execute_api_vpce_id" { type = string }
variable "flags_table_name" { type = string }
variable "flags_table_arn" { type = string }
variable "handler_path" { type = string }

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
