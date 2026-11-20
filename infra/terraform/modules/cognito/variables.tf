variable "environment" { type = string }

variable "domain_suffix" {
  description = "Sufijo único global para el dominio del Hosted UI."
  type        = string
}

variable "callback_urls" {
  type = list(string)
}
