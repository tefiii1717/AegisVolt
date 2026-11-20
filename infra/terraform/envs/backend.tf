# Estado remoto en S3 con versionamiento (lo crea infra/terraform/bootstrap).
# El bucket y la región se inyectan con -backend-config=backend/aws.hcl (o backend/floci.hcl).
#
# Con workspaces, Terraform guarda cada ambiente en una ruta distinta del MISMO bucket:
#   env:/dev/aegisvolt/platform.tfstate
#   env:/test/aegisvolt/platform.tfstate
#   env:/prod/aegisvolt/platform.tfstate
# `terraform workspace select test` hace que plan/apply SOLO lean y escriban env:/test/...
terraform {
  backend "s3" {
    key     = "aegisvolt/platform.tfstate"
    encrypt = true
    # El mecanismo de bloqueo va en el archivo -backend-config:
    #   floci.hcl → tabla DynamoDB aegisvolt-tflock (como la suite de compatibilidad de Floci)
    #   aws.hcl   → use_lockfile = true (bloqueo nativo con archivo .tflock en S3)
  }
}
