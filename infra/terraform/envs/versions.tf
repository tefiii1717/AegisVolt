terraform {
  required_version = ">= 1.10.0" # >= 1.10 para el bloqueo nativo de estado en S3 (use_lockfile)
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.80" }
    archive = { source = "hashicorp/archive", version = "~> 2.6" }
    helm    = { source = "hashicorp/helm", version = "~> 2.16" }
  }
}
