# 05 · Todo en Floci (sin cuenta de AWS)

Los tres ambientes (dev, test, prod) se aprovisionan con Terraform contra **Floci**, el emulador
local de AWS (puerto 4566). Verificado en CI: el apply crea **134 recursos** en el workspace `dev`
(VPC, subredes, tablas de rutas, Gateway/Interface VPC Endpoints, NLB, API Gateway v2 + VPC Link +
autorizador JWT, Cognito, DynamoDB, KMS, Lambda + REST API privada, CloudWatch Logs y un clúster
**EKS emulado con k3s**), y el bucket de estado tiene versionamiento `Enabled`.

## Puesta en marcha (WSL2 / Ubuntu)

Requisitos: Docker, Terraform ≥ 1.10, AWS CLI v2, jq, kubectl y helm.

```bash
# 1. Floci con datos persistentes (volumen aegisvolt-floci-data)
scripts/floci/terraform.sh up

# 2. Estado remoto: bucket S3 versionado + tabla de bloqueo DynamoDB
scripts/floci/terraform.sh bootstrap

# 3. Ambientes (cada uno en su workspace y su ruta env:/<ws>/ del bucket)
scripts/floci/terraform.sh dev plan
scripts/floci/terraform.sh dev apply
scripts/floci/terraform.sh test apply
scripts/floci/terraform.sh prod apply

# 4. Evidencia para la sustentación
scripts/floci/terraform.sh dev versions                 # historial del tfstate
scripts/floci/terraform.sh dev restore <VERSION_ID>     # restaurar una versión anterior

# 5. Kubernetes del ambiente (EKS emulado) + Argo Rollouts, ingress-nginx y Strimzi
scripts/floci/k8s-platform.sh dev

# 6. AegisVolt en el clúster, por etapas
scripts/floci/deploy-k8s.sh dev check    # los pods alcanzan a Floci
scripts/floci/deploy-k8s.sh dev kafka    # Kafka de 1 broker + 11 tópicos
scripts/floci/deploy-k8s.sh dev images   # build + importación al containerd del k3s
scripts/floci/deploy-k8s.sh dev seed     # estaciones, billeteras y flags
scripts/floci/deploy-k8s.sh dev helm     # chart; con imágenes nuevas → canary con análisis
scripts/floci/deploy-k8s.sh dev e2e      # SAGA feliz + rollback por flag, verificada en DynamoDB
kubectl argo rollouts list rollouts -n aegisvolt
```

Consola web de Floci: `http://localhost:4566/_floci/ui`.

## CI/CD con Floci

- **CI (`terraform-plan`)**: cada PR levanta un Floci efímero en el runner, hace el bootstrap,
  `plan` y `apply` sobre el workspace de la rama destino y publica el historial de versiones del
  tfstate. Es una prueba real del IaC sin costo.
- **CD**: corre en el runner indicado por la variable de repositorio `DEPLOY_RUNS_ON`.
  - Sin definir → `ubuntu-latest` (Floci efímero: verifica, pero el estado no persiste).
  - Runner propio en WSL2 → los ambientes y el historial del tfstate quedan vivos para la demo:
    1. GitHub → Settings → Actions → Runners → *New self-hosted runner* (Linux) y sigue los pasos
       en WSL2 agregando la etiqueta `floci`.
    2. Variable de repositorio `DEPLOY_RUNS_ON = ["self-hosted","floci"]`.
    3. En cada GitHub Environment (`dev`, `test`, `prod`): `KUBE_CONTEXT = aegisvolt-<env>`,
       `FLOCI_ENDPOINT_IN_CLUSTER = http://<IP de WSL2>:4566` e `IMAGE_REGISTRY` si usas un registro local.

## Qué cambia frente a AWS real

| Tema | En Floci | En AWS real (código ya preparado, `use_floci = false`) |
|---|---|---|
| EKS | `modules/eks-floci`: `aws_eks_cluster` + nodo k3s | `modules/eks` (módulo oficial, node groups, IRSA) |
| Credenciales de pods | variables `AWS_ACCESS_KEY_ID=test` vía Helm (`global.awsEndpoint`) | IRSA: un rol IAM por ServiceAccount |
| Bloqueo del estado | tabla DynamoDB `aegisvolt-tflock` | `use_lockfile` nativo de S3 |
| Análisis del canary | proveedor `web` (`/health/stats` del Service `-canary`) | `web` o `cloudwatch` |
| Imágenes | build local por tag de contenido | ECR (bootstrap con `create_ecr = true`) |

## Verificado en WSL2 (20/11/2026)

- 134 recursos en `dev`, 5 versiones del tfstate en `env:/dev/`.
- EKS emulado con nodo k3s `Ready`; Argo Rollouts, ingress-nginx y Strimzi en `Running`.
- 8 servicios `Healthy`; canary al 20 % con `AnalysisRun Successful` (3 mediciones) y un aborto
  real con rollback automático cuando el análisis no pudo medir.
- E2E en el clúster: `3 passed` (scopes, SAGA completa y compensación con la flag de falla).

## Problemas conocidos y su solución

| Síntoma | Causa | Solución aplicada |
|---|---|---|
| Helm se cuelga descargando charts | WSL2 sin salida IPv6 | IPv6 desactivado en WSL2 y charts por `curl -4` |
| Kafka `Pending` | el k3s de Floci no trae StorageClass | almacenamiento efímero (`kafka-single-node.yaml`) |
| Pods reiniciados por la startup probe | el arranque esperaba a Kafka | conexión a Kafka en segundo plano |
| `dynamodb: DOWN` en readiness | NetworkPolicy solo permitía 443 | regla de salida al puerto de Floci |
| AnalysisRun en `Error` | URL corta resuelta desde `argo-rollouts` | FQDN `<svc>-canary.aegisvolt.svc.cluster.local` |
| Conflictos al aplicar el chart | Helm 4 (server-side apply) vs. Argo | `--force-conflicts` |
