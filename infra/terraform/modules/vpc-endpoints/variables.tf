variable "name" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "private_route_table_ids" { type = list(string) }

variable "interface_services" {
  description = "Interface VPCE a crear. execute-api, logs y monitoring son el mínimo del proyecto."
  type        = list(string)
  default     = ["execute-api", "logs", "monitoring", "sts", "ecr.api", "ecr.dkr", "ec2", "eks", "eks-auth", "elasticloadbalancing"]
}
