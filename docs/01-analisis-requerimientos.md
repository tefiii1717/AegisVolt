# 01 · Análisis de requerimientos: incompatibilidades y mejoras

> Documento de trabajo de AegisVolt. Revisa la especificación oficial del Proyecto Final
> (Patrones Arquitectónicos Avanzados) y los requisitos adicionales que agregué
> (Kafka, Outbox, ACL con Kafka Streams, Strangler Fig, CQRS, Circuit Breaker, Sidecar, OAuth2/JWT,
> seguridad de cadena de suministro). Cada hallazgo indica **qué dice el requisito**, **por qué es
> problemático** y **la decisión que toma este repositorio**.

Severidad: 🔴 bloqueante (no se puede implementar tal como está escrito) · 🟠 ambiguo o riesgoso ·
🟡 menor / editorial.

---

## 🔴 1. "VPC Endpoint hacia API Gateway y hacia Kubernetes" (sección 2 y 2.4)

**Qué dice:** el diagrama muestra `API GATEWAY AWS → (VPC Endpoint Privado) → EKS`, y 2.4 pide
"VPCE que conecten de forma privada hacia los endpoints de API Gateway y hacia los servicios del clúster".

**Problema:** un *Interface VPC Endpoint* (`com.amazonaws.<region>.execute-api`) sirve para el tráfico
que **sale de la VPC hacia** una API privada de API Gateway. No sirve para que API Gateway **entre** a
la VPC. API Gateway llega a recursos privados mediante una **VPC Link** (HTTP API → VPC Link v2 →
ALB/NLB interno). Además, las *HTTP APIs* no pueden ser privadas: solo las *REST APIs* con
`endpoint_type = PRIVATE` se consumen a través de un VPCE.

**Decisión (AegisVolt):**

| Flujo | Mecanismo correcto |
|---|---|
| Navegador → plataforma | **HTTP API pública** (TLS, autorizador JWT de Cognito, throttling) |
| HTTP API → pods de EKS | **VPC Link v2 → NLB interno** (AWS Load Balancer Controller) **→ ingress-nginx → graphql-bff** |
| Prefect / Lambdas / jobs internos → API de operaciones | **REST API privada** (`/ops/*`) consumida solo vía **Interface VPCE `execute-api`** con *resource policy* `aws:SourceVpce` |
| Consumidores de otras VPC → servicios del clúster | **VPC Endpoint Service (PrivateLink)** delante del NLB interno (opcional, `enable_privatelink_service`) |
| Pods → DynamoDB / S3 | **Gateway VPCE** (entrada en la tabla de rutas privada con *prefix list*) |
| Pods → CloudWatch Logs, ECR, STS | **Interface VPCE** (ENI en subredes privadas + Security Group 443) |

Así se cumple la intención (el tráfico interno nunca sale a Internet) con los componentes que AWS sí
soporta. Este punto responde la pregunta 6.1 sobre "el recorrido de un paquete": ver
[`02-arquitectura.md` § Red](02-arquitectura.md#recorrido-de-un-paquete).

## 🔴 2. "Retención de saldo" vs "descuenta el saldo" (1.2 vs 2.3 Paso 2)

**Qué dice:** 1.2 habla de *autorizar retenciones de saldo*; 2.3 dice que el Paso 2 *descuenta el saldo*
y que la compensación lo *reembolsa*.

**Problema:** son dos modelos financieros distintos. Descontar y luego reembolsar genera dos
movimientos contables por cada fallo y no permite cobrar el consumo real (kWh) al final.

**Decisión:** modelo **Hold → Capture** (como una preautorización de tarjeta):

1. Paso 2 `AuthorizeHold`: mueve el estimado de `available` a `held` (el saldo disponible baja: cumple
   "descuenta el saldo estimado").
2. Compensación `ReleaseHold`: devuelve `held` a `available` y registra el evento `FundsRefunded`
   (mantiene el vocabulario de la rúbrica: *reembolso* + *evento de reversión*).
3. Tras el éxito del Paso 3 se envía `CaptureHold` (cobro definitivo).

## 🟠 3. La SAGA no define transacción pivote, captura ni timeouts (2.3)

**Problema:** una vez que el cargador entrega energía, eso no se puede "deshacer". La especificación
no dice qué pasa después del Paso 3, ni qué ocurre si un servicio nunca responde.

**Decisión:**

- `StartCharging` (Paso 3) es la **transacción pivote**: antes de ella todo es compensable; después
  de ella todo es **reintentable** (`CaptureHold`).
- Cada paso tiene **timeout** (por defecto 30 s). Si vence, el orquestador lo trata como fallo y
  compensa.
- **Bloqueo semántico / anti-"fantasma":** si llega una compensación antes que la acción (mensaje
  tardío), el servicio escribe una *lápida* (`RELEASED_EMPTY`) y rechaza la acción cuando llegue
  después. Esto evita retenciones huérfanas.
- **Idempotencia** (pregunta 6.3): cada comando lleva `message_id`; el servicio hace
  `TransactWriteItems` con `Put MSG#<id>` condicionado a `attribute_not_exists` + cambio de estado +
  respuesta en el *outbox*. Un duplicado cancela la transacción completa y no tiene efectos.

## 🟠 4. Tiempo real con "HTTPS / WSS" a través de API Gateway (sección 2)

**Problema:** las HTTP/REST APIs no hacen WebSocket; la *WebSocket API* es otro tipo de API Gateway
que no habla el protocolo de suscripciones GraphQL, y las HTTP APIs **almacenan en búfer** la
respuesta (no hay SSE real). Además el *timeout* de integración es de 30 s.

**Decisión:** el esquema GraphQL declara `Subscription` (usable localmente vía SSE), pero el frontend
usa **polling de queries cada 2–3 s**, que funciona igual en local y detrás de API Gateway. Para
"tiempo real" estricto en la nube la alternativa sería AWS AppSync, que reemplazaría al BFF (fuera de
alcance).

## 🟠 5. Canary 10–20 % "en Kubernetes" (3.3.b)

**Problemas:**

1. Un `Deployment` no reparte tráfico por porcentaje: solo por proporción de réplicas.
2. Tres de los servicios **no reciben HTTP**: consumen comandos de Kafka. Para ellos "porcentaje de
   tráfico" = proporción de **particiones** asignadas al grupo de consumidores.
3. Durante el canary conviven dos versiones procesando la misma SAGA → los mensajes deben ser
   **compatibles hacia atrás**.

**Decisión:** **Argo Rollouts**.

- `graphql-bff` (HTTP): canary con `trafficRouting.nginx` → peso exacto 20 % → análisis → 50 % → 100 %.
- Servicios consumidores de Kafka: canary por réplicas (1 de 5 pods ≈ 20 % de las particiones).
- `AnalysisTemplate` con proveedor **CloudWatch**: tasa de errores (métrica de filtro de logs
  `level=ERROR`) < 5 % y p95 de latencia, 3 mediciones cada 60 s. Si falla → *abort* y rollback
  automático.
- Sobre de mensajes versionado (`type`, `version`) y consumidores tolerantes a campos desconocidos.

## 🟠 6. Gobernanza de ramas: lo que GitHub sí y no puede bloquear (3.1 / 3.2)

- **"Prohibido bajo cualquier circunstancia":** el dueño del repo es administrador y por defecto
  puede saltarse las reglas. Los *rulesets* deben tener **`bypass_actors: []`**.
- **La cadena `dev → test → prod` no se puede expresar con reglas nativas** (GitHub no restringe la
  rama *origen* de un PR). Se agrega el check obligatorio **`promotion-guard`**, que falla si un PR a
  `test` no viene de `dev` o uno a `prod` no viene de `test`.
- **Squash en promociones rompe el historial:** si `dev → test` se fusiona con *squash*, `test`
  queda con commits que `dev` no tiene y la siguiente promoción genera conflictos (justo lo que la
  sección 3.2 quiere evitar). Regla: `feature/* → dev` con *squash*; `dev → test` y `test → prod`
  solo con **merge commit**.
- Los nombres de los checks obligatorios deben coincidir exactamente con los `name:` de los jobs.
- El repositorio es público, así que los rulesets aplican sin plan de pago.

## 🟠 7. Costo y factibilidad de 3 ambientes con EKS en AWS real (2.4)

Tres clústeres EKS (el plano de control cuesta del orden de US$0.10/h cada uno), nodos, NAT
Gateways e Interface VPCEs por AZ y por ambiente, durante 14 días, es caro para un proyecto
individual. Si se usa una cuenta de **AWS Academy Learner Lab**, además no se pueden crear roles IAM
(hay que reutilizar `LabRole`), lo que impide IRSA/OIDC.

**Decisión:**

- Terraform soporta **los tres ambientes completos**, pero con interruptores
  (`enable_eks`, `enable_interface_endpoints`, `nat_mode = "none" | "single"`).
- Sin NAT: los nodos salen a ECR/S3/STS/Logs **por VPC Endpoints** (refuerza el requisito de tráfico
  privado y ahorra el NAT).
- `dev` y `test` corren sobre **Floci** (emulador MIT con DynamoDB, S3, API Gateway v2, Cognito y EKS
  respaldado por k3s); `prod` en AWS real solo durante la demo y luego `terraform destroy`.
- Variable `existing_iam_role_arn` para cuentas sin permiso de crear roles.

## 🟠 8. "Uso exclusivo de DynamoDB" vs. Prefect y Kafka (2.4 / 2.5)

El servidor de Prefect necesita su propia base (SQLite/PostgreSQL) y Kafka persiste sus logs. La
exclusividad se interpreta como **datos de negocio**: todo dato de dominio vive en DynamoDB
(*Database-per-Service*). Prefect usa SQLite en un volumen (o Prefect Cloud) y Kafka es transporte.

## 🟠 9. `terraform plan` en el PR vs `apply -auto-approve` tras el merge (3.3)

El plan del PR puede quedar obsoleto si otro PR se fusiona antes. Mitigaciones:
`concurrency` por ambiente en el pipeline (nunca dos `apply` simultáneos al mismo workspace),
bloqueo nativo de estado en S3 (`use_lockfile = true`) y re-plan dentro del job de `apply`.
`prod` usa un *GitHub Environment* con revisores obligatorios: el `apply` sigue siendo
`-auto-approve` para Terraform, pero el job espera aprobación humana.

## 🟡 10. Detalles editoriales

- La numeración salta de la sección 3 a la 5: **falta la sección 4** (¿entregables?). Conviene
  preguntar al profesor.
- 6.4 dice 20 %, que corresponde a 12 % + 8 % de la tabla 5.2: es consistente, solo está agrupado.
- El diagrama ubica DynamoDB "en subred privada": DynamoDB es un servicio regional; lo privado es la
  **ruta** (Gateway VPCE), no la tabla.
- Se recomiendan CIDR distintos por workspace (`10.10/16`, `10.20/16`, `10.30/16`) para no impedir un
  futuro *peering*.

---

## Requisitos que agregué y cómo encajan sin crear un "monolito distribuido"

| Requisito | Dónde vive | Cuidado clave |
|---|---|---|
| Kafka (EDA) | Strimzi (KRaft) en EKS · `apache/kafka` en Compose · Redpanda vía Floci | Clave de partición = `saga_id` (orden por SAGA) |
| Transactional Outbox | Cada servicio escribe estado + mensaje en **una** `TransactWriteItems`; un *relay* publica | Entrega *at-least-once* → consumidores idempotentes |
| ACL con Kafka Streams | `acl-ocpp-translator` (Java) traduce OCPP 1.6 del legado al modelo de dominio | Mensajes inválidos → *dead-letter topic* |
| Strangler Fig | `legacy-csms` (sistema heredado) + flag `STRANGLER_ROUTE_STATIONS_TO_CORE` en el BFF | Migración gradual sin *big bang* |
| CQRS | `query-projection` construye modelos de lectura desde eventos | Consistencia eventual visible en la UI |
| Circuit Breaker | `energy-dispatch` → cargador; BFF → servicios | Estados CERRADO / ABIERTO / SEMI-ABIERTO expuestos en `/health` |
| Sidecar | Fluent Bit (logs → CloudWatch) y *outbox relay* como contenedores sidecar | Sidecars nativos de K8s (`restartPolicy: Always`) |
| OAuth2 / JWT / Scopes | Cognito (client credentials + PKCE), autorizador JWT en API Gateway, scopes por campo en el BFF | Defensa en profundidad |
| AWS Lambda | `flags-admin` (cambiar flags sin redesplegar) | Flags con `prod_locked` no se pueden cambiar en prod |

**Anti-monolito distribuido:** sin base compartida; sin llamadas síncronas entre servicios de dominio
(solo comandos/eventos); la librería común contiene **solo infraestructura** (logs, sobre, outbox),
nunca modelos de dominio; cada servicio se construye, versiona y despliega de forma independiente.

## Vulnerabilidades recientes consideradas

- **React2Shell (CVE-2025-55182, CVSS 10):** deserialización insegura en React Server Components
  (`react-server-dom-*`). El frontend es una SPA con Vite (sin RSC ni Next.js App Router), así que no
  expone el protocolo Flight; aun así se fija `react >= 19.2.3` y CI ejecuta `npm audit`.
- **Inyección en CI/CD:** ningún `${{ github.event.* }}` dentro de `run:`; no se usa
  `pull_request_target`; acciones fijadas por SHA; `permissions` mínimos; OIDC hacia AWS con
  *trust policy* restringida a `repo:tefiii1717/AegisVolt:environment:<env>`.
- **Deserialización en Python:** prohibido `pickle`, `yaml.load` y `eval` sobre datos externos
  (Bandit en CI). Los mensajes Kafka se validan con Pydantic en modo estricto y tamaño máximo.
  Prefect se configura con `result_serializer="json"` (por defecto usa pickle).

## Prioridad recomendada (según pesos de la rúbrica)

1. IaC (30 %): workspaces, backend S3 versionado, VPC/VPCE, DynamoDB.
2. CI/CD (25 %): rulesets, promotion-guard, plan/apply dinámico, canary.
3. Backend (25 %): SAGA demostrable con la flag de fallo, idempotencia, flags por ambiente.
4. Observabilidad (12 %): logs JSON con `correlation_id`, flows de Prefect.
5. Frontend (8 %).

Kafka, ACL, Strangler y CQRS suman nivel, pero la rúbrica no les asigna peso directo: se construyen
sin poner en riesgo los puntos 1 y 2.
