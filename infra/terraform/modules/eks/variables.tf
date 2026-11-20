variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "nlb_security_group_id" { type = string }

variable "ingress_port" {
  type    = number
  default = 80
}

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}

variable "public_access_cidrs" {
  type    = list(string)
  default = []
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "node_capacity_type" {
  type    = string
  default = "SPOT"
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 4
}

variable "node_desired" {
  type    = number
  default = 2
}

variable "create_iam" {
  type    = bool
  default = true
}

variable "existing_iam_role_arn" {
  type    = string
  default = null
}
