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
