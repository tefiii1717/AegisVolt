#!/usr/bin/env bash
# Prepara el clúster EKS emulado por Floci (k3s) de un ambiente para recibir el chart de AegisVolt.
#   scripts/floci/k8s-platform.sh dev
#
# 1. Floci rechaza test/test para EKS: se crea un usuario IAM del emulador con access key propia y se
#    guarda en el perfil de AWS CLI "floci-k8s" (con endpoint_url de Floci).
# 2. kubeconfig del clúster aegisvolt-<env>: el exec `aws eks get-token` usa ese perfil (AWS_PROFILE).
# 3. Complementos: Argo Rollouts (canary), ingress-nginx (pesos del BFF) y el operador Strimzi (Kafka).
set -euo pipefail
ENV="${1:?uso: k8s-platform.sh <dev|test|prod>}"
FLOCI="${FLOCI_ENDPOINT:-http://localhost:4566}"
CLUSTER="aegisvolt-$ENV"
PROFILE="floci-k8s"
admin() { AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 aws --endpoint-url "$FLOCI" "$@"; }

USER_NAME="aegisvolt-k8s-admin"
admin iam get-user --user-name "$USER_NAME" >/dev/null 2>&1 || admin iam create-user --user-name "$USER_NAME" >/dev/null
if ! aws configure get aws_access_key_id --profile "$PROFILE" >/dev/null 2>&1; then
  key_json="$(admin iam create-access-key --user-name "$USER_NAME" --output json)"
  aws configure set aws_access_key_id "$(jq -r .AccessKey.AccessKeyId <<<"$key_json")" --profile "$PROFILE"
  aws configure set aws_secret_access_key "$(jq -r .AccessKey.SecretAccessKey <<<"$key_json")" --profile "$PROFILE"
  aws configure set region us-east-1 --profile "$PROFILE"
  aws configure set endpoint_url "$FLOCI" --profile "$PROFILE"
  echo "Perfil AWS CLI '$PROFILE' creado (solo válido contra Floci)."
fi
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

aws eks wait cluster-active --name "$CLUSTER" --profile "$PROFILE"
aws eks update-kubeconfig --name "$CLUSTER" --alias "$CLUSTER" --profile "$PROFILE"
kubectl config use-context "$CLUSTER"
kubectl get nodes -o wide

# Los charts se descargan con curl por IPv4 y Helm instala desde el archivo local: evita que el
# cliente HTTP de Helm se cuelgue con release-assets.githubusercontent.com en WSL2.
CHARTS="${XDG_CACHE_HOME:-$HOME/.cache}/aegisvolt/charts"
mkdir -p "$CHARTS"
chart() {
  local url="$1" file="$CHARTS/$(basename "$1")"
  [[ -s "$file" ]] || curl -4 -fsSL --retry 3 --retry-delay 2 --max-time 120 -o "$file" "$url"
  echo "$file"
}

helm upgrade --install argo-rollouts \
  "$(chart https://github.com/argoproj/argo-helm/releases/download/argo-rollouts-2.37.7/argo-rollouts-2.37.7.tgz)" \
  -n argo-rollouts --create-namespace --wait --timeout 10m
helm upgrade --install ingress-nginx \
  "$(chart https://github.com/kubernetes/ingress-nginx/releases/download/helm-chart-4.11.3/ingress-nginx-4.11.3.tgz)" \
  -n ingress-nginx --create-namespace --set controller.service.type=NodePort --wait --timeout 10m
helm upgrade --install strimzi \
  "$(chart https://github.com/strimzi/strimzi-kafka-operator/releases/download/0.44.0/strimzi-kafka-operator-helm-3-chart-0.44.0.tgz)" \
  -n kafka --create-namespace --set 'watchNamespaces={kafka}' --wait --timeout 10m
kubectl get pods -A --no-headers | awk '{print $1, $2, $4}' | column -t
echo "Clúster $CLUSTER listo. Configura en el GitHub Environment '$ENV': KUBE_CONTEXT=$CLUSTER"
