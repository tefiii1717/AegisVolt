#!/usr/bin/env bash
# Despliega AegisVolt en el EKS emulado por Floci (k3s), por etapas:
#   scripts/floci/deploy-k8s.sh dev check    # el clúster alcanza a Floci (DynamoDB, Logs)
#   scripts/floci/deploy-k8s.sh dev kafka    # Kafka de 1 broker + tópicos (Strimzi)
#   scripts/floci/deploy-k8s.sh dev images   # build local + importación al containerd del k3s
#   scripts/floci/deploy-k8s.sh dev seed     # datos semilla en las tablas DynamoDB del ambiente
#   scripts/floci/deploy-k8s.sh dev helm     # chart (Argo Rollouts); con imágenes nuevas → canary
#   scripts/floci/deploy-k8s.sh dev status   # estado de pods y rollouts
set -euo pipefail
ENV="${1:?uso: deploy-k8s.sh <dev|test|prod> <check|kafka|images|seed|helm|status>}"
STEP="${2:?indica la etapa}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
CLUSTER="aegisvolt-$ENV"
SERVICES=(station-reservation billing-wallet energy-dispatch saga-orchestrator query-projection legacy-csms graphql-bff)
kubectl config use-context "$CLUSTER" >/dev/null

k3s_container() {
  docker ps --format '{{.Names}}' | grep -E "eks.*${CLUSTER}|${CLUSTER}.*k3s" | head -1
}

# Floci visto desde los pods: gateway de la red Docker del contenedor k3s + puerto publicado 4566.
floci_from_cluster() {
  local gw
  gw=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}} {{end}}' "$(k3s_container)" | awk '{print $1}')
  echo "http://${gw}:4566"
}

tag_of() {
  jq -r --arg n "$1" '.[] | select(.name == $n) | .paths' scripts/ci/images.json | xargs scripts/ci/image-tag.sh
}

case "$STEP" in
  check)
    echo "Contenedor k3s: $(k3s_container)"
    endpoint=$(floci_from_cluster)
    echo "Floci desde el clúster: $endpoint"
    kubectl run floci-check --rm -i --restart=Never --image=curlimages/curl:8.11.1 -n default --quiet -- \
      curl -s -o /dev/null -w "HTTP %{http_code}\n" --max-time 10 "$endpoint/_localstack/health" ;;
  kafka)
    kubectl apply -f deploy/k8s/platform/namespace.yaml
    kubectl apply -f deploy/k8s/platform/kafka-single-node.yaml
    echo "Esperando a que Kafka esté listo (puede tardar 3-5 min la primera vez)..."
    kubectl wait kafka/aegisvolt -n kafka --for=condition=Ready --timeout=15m
    kubectl get kafkatopics -n kafka --no-headers | awk '{print $1, $NF}' | column -t ;;
  images)
    k3s=$(k3s_container)
    for svc in "${SERVICES[@]}"; do
      img=$(jq -c --arg n "$svc" '.[] | select(.name == $n)' scripts/ci/images.json)
      ctx=$(jq -r .context <<<"$img"); file=$(jq -r .dockerfile <<<"$img"); args=$(jq -r .args <<<"$img")
      ref="aegisvolt/$svc:$(tag_of "$svc")"
      build_args=(); [[ -n "$args" ]] && build_args=(--build-arg "$args")
      echo "▶ $ref"
      docker build -q --build-arg "APP_VERSION=${ref##*:}" "${build_args[@]}" -t "$ref" -f "$file" "$ctx" >/dev/null
      docker save "$ref" | docker exec -i "$k3s" ctr -n k8s.io images import - >/dev/null
    done
    docker exec "$k3s" ctr -n k8s.io images ls -q | grep aegisvolt ;;
  seed)
    docker run --rm --network host \
      -v "$ROOT/scripts:/opt/aegis/scripts:ro" -v "$ROOT/deploy/seed:/opt/aegis/deploy/seed:ro" \
      -e DYNAMODB_ENDPOINT=http://localhost:4566 -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_REGION=us-east-1 \
      "aegisvolt/saga-orchestrator:$(tag_of saga-orchestrator)" python /opt/aegis/scripts/bootstrap.py --env "$ENV" ;;
  helm)
    set_tags=()
    for svc in "${SERVICES[@]}"; do set_tags+=(--set "services.$svc.imageTag=$(tag_of "$svc")"); done
    # En el portátil: 2 réplicas por consumidor Kafka (en el clúster real son 5 → canary del 20 % exacto).
    for svc in station-reservation billing-wallet energy-dispatch saga-orchestrator query-projection; do
      set_tags+=(--set "services.$svc.replicas=${LOCAL_REPLICAS:-2}")
    done
    # --force-conflicts: Helm 4 aplica del lado del servidor y Argo Rollouts también escribe en los
    # Rollouts (pasos del canary); Helm toma la propiedad de esos campos en cada despliegue.
    helm upgrade --install aegisvolt deploy/helm/aegisvolt -n aegisvolt --force-conflicts \
      -f "deploy/helm/aegisvolt/values-$ENV.yaml" \
      --set global.registry=aegisvolt --set global.authMode=dev \
      --set global.awsEndpoint="$(floci_from_cluster)" \
      --set global.kafkaBootstrap=aegisvolt-kafka-bootstrap.kafka.svc:9092 \
      --set global.corsOrigins=http://localhost:5173 "${set_tags[@]}"
    kubectl get rollouts -n aegisvolt ;;
  status)
    kubectl get pods -n aegisvolt -o wide --no-headers | awk '{print $1, $2, $3, $4}' | column -t
    kubectl get rollouts -n aegisvolt ;;
  *) echo "etapa inválida: $STEP" >&2; exit 2 ;;
esac
