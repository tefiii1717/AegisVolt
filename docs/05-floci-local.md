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

Pendiente de verificar en tu máquina: `k8s-platform.sh` (acceso al k3s con la access key del
emulador) y el despliegue del chart sobre ese clúster; el CI no lo ejercita porque requiere un
clúster persistente.
