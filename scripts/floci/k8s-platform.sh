#!/usr/bin/env bash
# Prepara el clúster EKS emulado por Floci (k3s) de un ambiente para recibir el chart de AegisVolt.
#   scripts/floci/k8s-platform.sh dev
#
# 1. Floci rechaza test/test para cluster-admin: se crea un usuario IAM con access key real del emulador.
# 2. kubeconfig del clúster aegisvolt-<env> (contexto "aegisvolt-<env>").
# 3. Complementos: Argo Rollouts (canary), ingress-nginx (pesos del BFF) y el operador Strimzi (Kafka).
set -euo pipefail
ENV="${1:?uso: k8s-platform.sh <dev|test|prod>}"
FLOCI="${FLOCI_ENDPOINT:-http://localhost:4566}"
CLUSTER="aegisvolt-$ENV"
export AWS_DEFAULT_REGION=us-east-1 AWS_REGION=us-east-1
admin() { AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test aws --endpoint-url "$FLOCI" "$@"; }

USER="aegisvolt-k8s-admin"
admin iam get-user --user-name "$USER" >/dev/null 2>&1 || admin iam create-user --user-name "$USER" >/dev/null
CREDS_FILE="$HOME/.aegisvolt/floci-k8s-admin.json"
mkdir -p "$(dirname "$CREDS_FILE")" && chmod 700 "$(dirname "$CREDS_FILE")"
if [[ ! -s "$CREDS_FILE" ]]; then
  admin iam create-access-key --user-name "$USER" --output json > "$CREDS_FILE"
  chmod 600 "$CREDS_FILE"
fi
export AWS_ACCESS_KEY_ID="$(jq -r .AccessKey.AccessKeyId "$CREDS_FILE")"
export AWS_SECRET_ACCESS_KEY="$(jq -r .AccessKey.SecretAccessKey "$CREDS_FILE")"

aws --endpoint-url "$FLOCI" eks wait cluster-active --name "$CLUSTER"
aws --endpoint-url "$FLOCI" eks update-kubeconfig --name "$CLUSTER" --alias "$CLUSTER"
kubectl config use-context "$CLUSTER"
kubectl get nodes

helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null
helm repo add strimzi https://strimzi.io/charts/ >/dev/null
helm repo update >/dev/null
helm upgrade --install argo-rollouts argo/argo-rollouts --version 2.37.7 -n argo-rollouts --create-namespace --wait
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx --version 4.11.3 -n ingress-nginx --create-namespace \
  --set controller.service.type=NodePort --wait
helm upgrade --install strimzi strimzi/strimzi-kafka-operator --version 0.44.0 -n kafka --create-namespace \
  --set 'watchNamespaces={kafka}' --wait
echo "Clúster $CLUSTER listo. Configura en el GitHub Environment '$ENV': KUBE_CONTEXT=$CLUSTER"
