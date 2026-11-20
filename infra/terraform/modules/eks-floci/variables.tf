variable "environment" { type = string }
variable "private_subnet_ids" { type = list(string) }

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}
