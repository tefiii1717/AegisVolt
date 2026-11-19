variable "region" {
  type    = string
  default = "us-east-1"
}

variable "use_floci" {
  description = "true (por defecto) = todo se aprovisiona en Floci, el emulador local de AWS; false = AWS real."
  type        = bool
  default     = true
}

variable "floci_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

variable "enable_eks" {
  description = "Crear el clúster EKS y sus complementos. Permite apagar el costo de un ambiente sin perder el resto."
  type        = bool
  default     = true
}

variable "enable_interface_endpoints" {
  type    = bool
  default = true
}

variable "nat_mode" {
  type    = string
  default = "none"
}

variable "create_iam" {
  description = "false en cuentas donde no se pueden crear roles (AWS Academy Learner Lab → usar LabRole)."
  type        = bool
  default     = true
}

variable "existing_iam_role_arn" {
  type    = string
  default = null
}

variable "eks_public_access_cidrs" {
  description = "CIDR autorizados a la API pública de EKS (runners de CI / IP de la estudiante)."
  type        = list(string)
  default     = []
}

variable "web_origins" {
  description = "Orígenes del frontend (CORS y callback de Cognito)."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "cognito_domain_suffix" {
  type    = string
  default = "tefi1717"
}
