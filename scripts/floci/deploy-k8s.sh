#!/usr/bin/env bash
# Despliega AegisVolt en el EKS emulado por Floci (k3s), por etapas:
#   scripts/floci/deploy-k8s.sh dev check    # el clúster alcanza a Floci (DynamoDB, Logs)
#   scripts/floci/deploy-k8s.sh dev kafka    # Kafka de 1 broker + tópicos (Strimzi)
#   scripts/floci/deploy-k8s.sh dev images   # build local + importación al containerd del k3s
#   scripts/floci/deploy-k8s.sh dev seed     # datos semilla en las tablas DynamoDB del ambiente
#   scripts/floci/deploy-k8s.sh dev helm     # chart (Argo Rollouts); con imágenes nuevas → canary
#   scripts/floci/deploy-k8s.sh dev e2e      # SAGA completa (feliz + rollback por flag) contra el clúster
#   scripts/floci/deploy-k8s.sh dev tunnels  # port-forwards persistentes (BFF y servicios)
#   scripts/floci/deploy-k8s.sh dev ui       # consola web en http://localhost:5173
#   scripts/floci/deploy-k8s.sh dev logs     # logs en CloudWatch (Floci) enviados por Fluent Bit
#   scripts/floci/deploy-k8s.sh dev prefect  # Prefect: salud + auditoría de SAGAs (UI en :4200)
#   scripts/floci/deploy-k8s.sh dev status   # estado de pods y rollouts
set -euo pipefail
ENV="${1:?uso: deploy-k8s.sh <dev|test|prod> <check|kafka|images|seed|helm|e2e|tunnels|ui|logs|prefect|status>}"
STEP="${2:?indica la etapa}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
CLUSTER="aegisvolt-$ENV"
SERVICES=(station-reservation billing-wallet energy-dispatch saga-orchestrator query-projection legacy-csms flags-admin graphql-bff)
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
      --set global.corsOrigins=http://localhost:5173 \
      --set global.flagsApiUrl=http://flags-admin:8000 "${set_tags[@]}"
    kubectl get rollouts -n aegisvolt ;;
  e2e)
    # SAGA de punta a punta contra el BFF del clúster (port-forward) y las tablas DynamoDB del ambiente.
    # El port-forward se ata a UN pod: si el BFF está en pleno canary, ese pod puede desaparecer.
    echo "Esperando a que el BFF termine su rollout..."
    kubectl argo rollouts status graphql-bff -n aegisvolt --timeout 10m
    kubectl port-forward -n aegisvolt svc/graphql-bff 4000:4000 >/tmp/aegis-pf.log 2>&1 &
    pf=$!; trap 'kill $pf 2>/dev/null' EXIT
    for _ in $(seq 1 30); do curl -fs http://localhost:4000/health/live >/dev/null && break; sleep 1; done
    curl -fs http://localhost:4000/health/live >/dev/null || { echo "el port-forward no responde:"; cat /tmp/aegis-pf.log; exit 1; }
    echo "BFF accesible en localhost:4000; ejecutando la SAGA de punta a punta..."
    docker run --rm --network host -v "$ROOT:/w:ro" -w /w -e E2E_ENV="$ENV" -e PYTHONDONTWRITEBYTECODE=1 \
      python:3.12-slim sh -c "pip install -q pytest httpx boto3 2>/dev/null && pytest -q -p no:cacheprovider -m e2e tests/e2e -o addopts=''" ;;
  tunnels)
    # Port-forwards persistentes (sobreviven al script) hacia los servicios que usan la UI y Prefect.
    pkill -f "kubectl port-forward -n aegisvolt" 2>/dev/null || true
    i=0
    for pair in graphql-bff:4000:4000 station-reservation:8101:8000 billing-wallet:8102:8000 energy-dispatch:8103:8000 \
                saga-orchestrator:8104:8000 query-projection:8105:8000; do
      IFS=: read -r svc local remote <<<"$pair"
      nohup kubectl port-forward -n aegisvolt "svc/$svc" "$local:$remote" >"/tmp/aegis-pf-$svc.log" 2>&1 &
      disown; i=$((i + 1))
    done
    sleep 4
    for p in 4000 8101 8102 8103 8104 8105; do
      printf "localhost:%s → " "$p"; curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 "http://localhost:$p/health/live"
    done ;;
  ui)
    # Frontend (nginx) apuntando al BFF del clúster vía el túnel de localhost:4000.
    docker build -q -t aegisvolt/frontend:local frontend >/dev/null
    docker rm -f aegisvolt-ui >/dev/null 2>&1 || true
    docker run -d --name aegisvolt-ui -p 5173:8080 \
      -e AEGIS_ENV="$ENV" -e GRAPHQL_URL=http://localhost:4000/graphql -e AUTH_MODE=dev \
      -e DEV_TOKEN_URL=http://localhost:4000/auth/dev-token -e REDIRECT_URI=http://localhost:5173/ \
      aegisvolt/frontend:local >/dev/null
    echo "Consola web: http://localhost:5173 (ábrela en el navegador de Windows)" ;;
  logs)
    # Fluent Bit (sidecar) → CloudWatch Logs de Floci: grupos y streams por servicio.
    export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
    for g in station-reservation-service billing-wallet-service energy-dispatch-service saga-orchestrator query-projection-service graphql-bff; do
      n=$(aws --endpoint-url http://localhost:4566 logs describe-log-streams --log-group-name "/aegisvolt/$ENV/$g" \
          --query 'length(logStreams)' --output text 2>/dev/null || echo "?")
      echo "/aegisvolt/$ENV/$g → $n streams"
    done
    echo "--- últimos eventos de la SAGA en el orquestador:"
    aws --endpoint-url http://localhost:4566 logs filter-log-events --log-group-name "/aegisvolt/$ENV/saga-orchestrator" \
      --filter-pattern '"transición de SAGA"' --max-items 5 --query 'events[].message' --output text 2>/dev/null \
      | tr '\t' '\n' | cut -c1-220 | tail -5 ;;
  prefect)
    # Servidor de Prefect (UI en :4200) + flujo de observabilidad: salud y auditoría de SAGAs del ambiente.
    docker start aegisvolt-prefect >/dev/null 2>&1 || docker run -d --name aegisvolt-prefect -p 4200:4200 \
      prefecthq/prefect:3-python3.12 prefect server start --host 0.0.0.0 >/dev/null
    for _ in $(seq 1 60); do curl -fs http://localhost:4200/api/health >/dev/null && break; sleep 2; done
    docker build -q -t aegisvolt/prefect-flows:local observability/prefect >/dev/null
    E="$(echo "$ENV" | tr a-z A-Z)"
    docker run --rm --network host -e PREFECT_API_URL=http://localhost:4200/api \
      -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test -e AWS_REGION=us-east-1 -e DYNAMODB_ENDPOINT=http://localhost:4566 \
      -e "BFF_URL_$E=http://localhost:4000" -e "STATION_RESERVATION_URL_$E=http://localhost:8101" \
      -e "BILLING_WALLET_URL_$E=http://localhost:8102" -e "ENERGY_DISPATCH_URL_$E=http://localhost:8103" \
      -e "SAGA_ORCHESTRATOR_URL_$E=http://localhost:8104" -e "ORCHESTRATOR_URL_$E=http://localhost:8104" \
      -e "QUERY_PROJECTION_URL_$E=http://localhost:8105" -e "QUERY_URL_$E=http://localhost:8105" \
      aegisvolt/prefect-flows:local python -c "from flows import observability; observability(['$ENV'])" 2>&1 \
      | grep -E "componentes en línea|orphan|Finished in state|Crashed|Failed" | cut -c1-200
    echo "UI de Prefect: http://localhost:4200 (Runs → aegisvolt-observability; Artifacts → saga-audit-$ENV)" ;;
  status)
    kubectl get pods -n aegisvolt -o wide --no-headers | awk '{print $1, $2, $3, $4}' | column -t
    kubectl get rollouts -n aegisvolt ;;
  *) echo "etapa inválida: $STEP" >&2; exit 2 ;;
esac
