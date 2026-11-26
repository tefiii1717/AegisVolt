# AegisVolt ⚡

Plataforma cloud-native de gestión de carga para flotas eléctricas y electrolineras B2B en la Sabana
de Bogotá. Proyecto final de **Patrones Arquitectónicos Avanzados** (Universidad de La Sabana).

Los operadores de flota reservan bahías de carga, el sistema retiene saldo y ordena al cargador
iniciar. Si algo falla, una **SAGA orquestada** reembolsa y libera la bahía automáticamente.

## Documentación

| Documento | Contenido |
|---|---|
| [01 · Análisis de requerimientos](docs/01-analisis-requerimientos.md) | Incompatibilidades del enunciado y cómo se resolvieron |
| [02 · Arquitectura](docs/02-arquitectura.md) | Bounded contexts, SAGA, CQRS, Strangler Fig, red, decisiones |
| [03 · Design Thinking](docs/03-design-thinking.md) | Personas, punto de vista, ideación, pruebas |
| [04 · Guía de sustentación](docs/04-guia-sustentacion.md) | Respuestas y demos para cada pregunta del banco §6 |
| [05 · Floci](docs/05-floci-local.md) | Los tres ambientes en el emulador local, CI/CD y diferencias con AWS |
| [06 · Identidad y facturación](docs/06-identidad-y-facturacion.md) | Roles por dominio de correo, medios de pago y facturas |

## Estructura

```
libs/aegis-common/         infraestructura compartida (logs JSON, sobre, outbox, idempotencia, flags, circuit breaker)
services/
  station-reservation/     Paso 1 de la SAGA + compensación (liberar bahía)
  billing-wallet/          Paso 2: retención Hold→Capture + reembolso
  energy-dispatch/         Paso 3 (pivote): activación física, telemetría, circuit breaker
  saga-orchestrator/       máquina de estados, timeouts, log de SAGA
  query-projection/        CQRS: modelos de lectura desde eventos
  legacy-csms/             sistema heredado (Strangler Fig), OCPP 1.6
streams/acl-ocpp-translator/  capa anticorrupción con Kafka Streams (Java)
gateway/graphql-bff/       BFF GraphQL: JWT/scopes, agregación, strangler, circuit breakers
serverless/flags-admin/    Lambda de Feature Flags (REST API privada vía VPCE)
frontend/                  consola web React + Vite + Tailwind
observability/prefect/     flows de salud y auditoría de SAGAs
infra/terraform/           bootstrap (S3 versionado, ECR, OIDC) + módulos + raíz con workspaces
deploy/                    Docker, Compose, Helm (Argo Rollouts), Kafka (Strimzi)
.github/                   CI (PR), CD (merge), rulesets de ramas
```

## Ejecutar en local

Requisitos: Docker con Compose v2.

```bash
docker compose -f deploy/compose/docker-compose.yml up -d --build
# Consola web:   http://127.0.0.1:5173   (crea una cuenta @operador; admin inicial: admin@admin.aegisvolt.co / AegisLocal2026)
# GraphiQL:      http://localhost:4000/graphql
# Floci (AWS):   http://localhost:4566/_floci/ui
# Prefect:       docker compose -f deploy/compose/docker-compose.yml --profile observability up -d → http://localhost:4200
```

Demos rápidas:
- **Rollback de la SAGA:** como administrador, Feature flags → activar *Simular falla física del cargador*; luego, como operador, Reservar carga.
- **Cupo insuficiente:** reservar 200 kWh varias veces seguidas con el mismo medio de pago hasta agotar su cupo.
- **Circuit breaker:** `curl -X POST localhost:8090/admin/chaos -H 'content-type: application/json' -d '{"failure_rate":1}'`
  y reservar tres veces; la tercera se compensa sin llamar al legado (Plataforma → circuito abierto).

## Pruebas

```bash
pip install -e "libs/aegis-common[test]"
pytest                                                    # librería + SAGA completa en proceso (moto)
(cd services/saga-orchestrator && python -m pytest -q --rootdir . tests)
(cd services/billing-wallet && python -m pytest -q --rootdir . tests)
(cd gateway/graphql-bff && npm ci && npm test)
(cd streams/acl-ocpp-translator && mvn test)
E2E_BFF_URL=http://localhost:4000 pytest -m e2e tests/e2e -o addopts=""   # con el stack de Compose arriba
```

## Infraestructura (Floci, sin cuenta de AWS)

```bash
scripts/floci/terraform.sh up            # Floci persistente en :4566
scripts/floci/terraform.sh bootstrap     # bucket de estado versionado + tabla de bloqueo
scripts/floci/terraform.sh dev apply     # workspace dev (también test y prod)
scripts/floci/terraform.sh dev versions  # historial del tfstate
scripts/floci/k8s-platform.sh dev        # EKS emulado (k3s) + Argo Rollouts, ingress-nginx, Strimzi
```

Guía completa en [05 · Floci](docs/05-floci-local.md). El código para AWS real se conserva
(`use_floci = false`, `backend/aws.hcl`).

## Flujo de ramas

`feature/*` → PR → `dev` → PR → `test` → PR → `prod`. Push directo y force push bloqueados por
rulesets (`bash scripts/github/apply-rulesets.sh`). Las promociones se fusionan con *merge commit*.

## Créditos de UI

Componentes adaptados de [Uiverse.io](https://uiverse.io) (licencia MIT): mapa con pines (SnyDeTreves),
checkout (SSpisso), batería cargando (doniaskima), aviso de éxito (akshat-patel28), alerta
(seyed-mohsen-mousavi) y camión de reparto (vikas7754, basado en jh3y).
