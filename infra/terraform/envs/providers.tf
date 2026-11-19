provider "aws" {
  region = var.region

  # Con Floci todas las APIs apuntan al emulador local.
  access_key                  = var.use_floci ? "test" : null
  secret_key                  = var.use_floci ? "test" : null
  skip_credentials_validation = var.use_floci
  skip_requesting_account_id  = var.use_floci
  skip_metadata_api_check     = var.use_floci
  s3_use_path_style           = var.use_floci

  dynamic "endpoints" {
    for_each = var.use_floci ? [1] : []
    content {
      apigateway     = var.floci_endpoint
      apigatewayv2   = var.floci_endpoint
      cloudwatch     = var.floci_endpoint
      cloudwatchlogs = var.floci_endpoint
      cognitoidp     = var.floci_endpoint
      dynamodb       = var.floci_endpoint
      ec2            = var.floci_endpoint
      elbv2          = var.floci_endpoint
      iam            = var.floci_endpoint
      kms            = var.floci_endpoint
      lambda         = var.floci_endpoint
      s3             = var.floci_endpoint
      sts            = var.floci_endpoint
    }
  }

  default_tags {
    tags = {
      Project     = "AegisVolt"
      Environment = local.env
      ManagedBy   = "Terraform"
      Workspace   = terraform.workspace
      Repository  = "tefiii1717/AegisVolt"
    }
  }
}

provider "helm" {
  kubernetes {
    host                   = try(module.eks[0].cluster_endpoint, "")
    cluster_ca_certificate = try(base64decode(module.eks[0].cluster_ca), "")
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", "aegisvolt-${local.env}", "--region", var.region]
    }
  }
}
