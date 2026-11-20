#!/usr/bin/env bash
# Terraform de AegisVolt contra Floci (emulador local de AWS, sin cuenta de AWS).
#
#   scripts/floci/terraform.sh up                       # levanta Floci con datos persistentes (si no está arriba)
#   scripts/floci/terraform.sh bootstrap                # bucket de estado VERSIONADO + tabla de bloqueo
#   scripts/floci/terraform.sh <dev|test|prod> plan     # plan en el workspace del ambiente
#   scripts/floci/terraform.sh <dev|test|prod> apply    # apply en el workspace del ambiente
#   scripts/floci/terraform.sh <dev|test|prod> versions # historial de versiones del tfstate en S3
#   scripts/floci/terraform.sh <dev|test|prod> restore <VERSION_ID>   # restaura una versión anterior
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="$ROOT/infra/terraform"
FLOCI="${FLOCI_ENDPOINT:-http://localhost:4566}"
BUCKET="aegisvolt-tfstate"
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 AWS_REGION=us-east-1
export TF_IN_AUTOMATION=1
aws_floci() { aws --endpoint-url "$FLOCI" "$@"; }

wait_floci() {
  for _ in $(seq 1 90); do
    curl -fs "$FLOCI/_localstack/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "Floci no responde en $FLOCI" >&2
  exit 1
}

up() {
  if curl -fs "$FLOCI/_localstack/health" >/dev/null 2>&1; then echo "Floci ya está arriba en $FLOCI"; return; fi
  docker run -d --name aegisvolt-floci -p 4566:4566 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v aegisvolt-floci-data:/app/data -e FLOCI_STORAGE_MODE=persistent \
    floci/floci:latest
  wait_floci
}

bootstrap() {
  wait_floci
  terraform -chdir="$TF/bootstrap" init -input=false
  terraform -chdir="$TF/bootstrap" apply -input=false -auto-approve
  local status
  status=$(aws_floci s3api get-bucket-versioning --bucket "$BUCKET" --query Status --output text)
  echo "Versionamiento del bucket $BUCKET: $status"
  [[ "$status" == "Enabled" ]] || { echo "::error::el bucket de estado no tiene versionamiento" >&2; exit 1; }
}

env_cmd() {
  local env="$1" action="$2"
  case "$env" in dev|test|prod) ;; *) echo "ambiente inválido: $env" >&2; exit 2 ;; esac
  wait_floci
  cd "$TF/envs"
  terraform init -input=false -reconfigure -backend-config=backend/floci.hcl >/dev/null
  terraform workspace select -or-create "$env"
  echo "Workspace activo: $(terraform workspace show)  →  estado en s3://$BUCKET/env:/$env/aegisvolt/platform.tfstate"
  case "$action" in
    plan)
      terraform plan -input=false -lock-timeout=5m -var-file="environments/$env.tfvars" -out=tfplan
      terraform show -no-color tfplan > plan.txt ;;
    apply)
      terraform apply -input=false -auto-approve -lock-timeout=10m -var-file="environments/$env.tfvars" ;;
    versions)
      aws_floci s3api list-object-versions --bucket "$BUCKET" --prefix "env:/$env/aegisvolt/platform.tfstate" \
        --query 'Versions[].{version:VersionId,fecha:LastModified,actual:IsLatest,bytes:Size}' --output table ;;
    restore)
      local version="${3:?indica el VersionId a restaurar}"
      aws_floci s3api copy-object --bucket "$BUCKET" --key "env:/$env/aegisvolt/platform.tfstate" \
        --copy-source "$BUCKET/env:/$env/aegisvolt/platform.tfstate?versionId=$version" >/dev/null
      echo "Versión $version restaurada como versión actual (la corrupta sigue en el historial)."
      terraform plan -input=false -var-file="environments/$env.tfvars" ;;
    *) echo "acción inválida: $action" >&2; exit 2 ;;
  esac
}

case "${1:-}" in
  up) up ;;
  bootstrap) bootstrap ;;
  dev|test|prod) env_cmd "$@" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
