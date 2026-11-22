# 04 · Guía de sustentación (banco de preguntas §6)

Respuestas cortas, con el archivo exacto donde está la evidencia y la demostración en vivo.

## 6.1 Infraestructura como Código (30 %)

**¿Cómo garantiza que `terraform apply` en `test` no sobrescriba DynamoDB ni el estado de `prod`?**
1. El ambiente no es una variable: es `terraform.workspace` (`infra/terraform/envs/locals.tf`). En el
   workspace `test` es imposible obtener la configuración de prod.
2. Cada workspace guarda su estado en una ruta distinta del bucket: `env:/test/aegisvolt/platform.tfstate`
   vs `env:/prod/...` (`backend.tf`). Un apply en test ni siquiera lee el estado de prod.
3. Todos los nombres llevan el ambiente: `aegisvolt-billing-test` ≠ `aegisvolt-billing-prod`.
4. En prod las tablas tienen `deletion_protection_enabled = true` y PITR.
5. El pipeline elige el workspace desde la rama que recibió el merge y usa `concurrency` por ambiente;
   el rol OIDC de prod solo lo puede asumir un job del GitHub Environment `prod`.

**Muestre versiones anteriores del tfstate de dev y cómo restauraría una.** (todo en Floci)
```bash
scripts/floci/terraform.sh dev versions
# equivale a:
aws --endpoint-url http://localhost:4566 s3api list-object-versions --bucket aegisvolt-tfstate \
  --prefix "env:/dev/aegisvolt/platform.tfstate" --query 'Versions[].{v:VersionId,fecha:LastModified,actual:IsLatest}' --output table
# restaurar: copiar la versión buena encima de la actual (queda como nueva versión; nada se pierde)
scripts/floci/terraform.sh dev restore <VERSION_ID>     # y luego revisa el plan que imprime
```
Evidencia: `aws_s3_bucket_versioning` en `infra/terraform/bootstrap/main.tf` (+ retención de 90 días);
el CI de cada PR imprime el historial de versiones del workspace destino.

**Recorrido de un paquete de API Gateway al pod.** Ver `docs/02-arquitectura.md#recorrido-de-un-paquete`.
Intervienen: SG `vpclink` (egress solo al puerto del NLB), SG del NLB (ingress solo desde `vpclink`),
regla `ingress_from_nlb` del SG de nodos, NetworkPolicies del namespace, y las tablas de rutas privadas
(solo `local` + prefix lists de los Gateway VPCE; sin `0.0.0.0/0`).

## 6.2 CI/CD y canary (25 %)

**Intente `git push --force` / push directo a `test`.**
```bash
git checkout test && git commit --allow-empty -m "prueba" && git push origin test
# remote: error: GH013: Repository rule violations found for refs/heads/test.
# - Changes must be made through a pull request.
git push --force origin test   # → bloqueado por la regla non_fast_forward
```
Reglas: `.github/rulesets/environment-branches.json` (`non_fast_forward`, `pull_request`,
`required_status_checks`, `bypass_actors: []`). Se aplican con `scripts/github/apply-rulesets.sh`.

**¿Cómo se determina el ambiente del `terraform apply` tras el merge?** `.github/workflows/cd.yml`: el
evento es `push` a `dev|test|prod`; `github.ref_name` (la rama que recibió el merge) se valida contra la
lista permitida y se usa en `environment:` y en `scripts/floci/terraform.sh "$TARGET_ENV" apply`, que
hace `terraform workspace select -or-create "$TARGET_ENV"` y usa `-var-file=environments/$TARGET_ENV.tfvars`.

**¿Qué métrica evalúa el canary antes de pasar del 20 % al 100 %?** `AnalysisTemplate canary-error-rate-web`
(`deploy/helm/aegisvolt/templates/platform.yaml`): consulta `/health/stats` a través del Service
`<servicio>-canary`, que Argo Rollouts apunta **solo a los pods de la versión nueva**, y lee
`errors_last_minute` (logs `ERROR` del último minuto). 3 mediciones cada 30 s; se exige ≤ N
(5 dev, 3 test, 1 prod). Si falla → Argo Rollouts aborta y todo vuelve a la versión estable; el job
`deploy-canary` queda en rojo. En AWS real existe la variante `canary-error-rate-cloudwatch`
(métrica `ErrorCount` con dimensión `Version`).

## 6.3 SAGA y Feature Flags (25 %)

**Demostración: flag de fallo en test + mutación + evidencia en DynamoDB.**
1. Consola web, ingresando con la cuenta `@admin` → Feature flags → activar *Simular falla física del cargador*
   (o la mutación `setFeatureFlag(name: "SIMULATE_CHARGER_HARDWARE_FAULT", enabled: true)`).
2. Ingresar con una cuenta `@operador` que tenga un medio de pago → Reservar carga → la línea de tiempo muestra: reserva ✔, retención ✔, activación ✖, reembolso ↩, bahía liberada ↩, y no se emite factura.
3. DynamoDB:
```bash
aws --endpoint-url http://localhost:4566 dynamodb get-item --table-name aegisvolt-billing-test --key '{"pk":{"S":"HOLD#<saga_id>"},"sk":{"S":"HOLD"}}'
# status = RELEASED, release_reason = HARDWARE_FAULT_SIMULATED
aws --endpoint-url http://localhost:4566 dynamodb query --table-name aegisvolt-billing-test --key-condition-expression "pk = :p AND begins_with(sk, :l)" \
  --expression-attribute-values '{":p":{"S":"WALLET#flota-andina"},":l":{"S":"LEDGER#"}}'
# asientos HOLD y REFUND (evento de reversión)
aws --endpoint-url http://localhost:4566 dynamodb get-item --table-name aegisvolt-reservations-test --key '{"pk":{"S":"RESERVATION#<saga_id>"},"sk":{"S":"RESERVATION"}}'
# status = RELEASED; el conector volvió a AVAILABLE
```

**¿Cómo evita problemas si una compensación se procesa dos veces?** Dos niveles
(`libs/aegis-common/aegis_common/dynamo.py::execute` y `services/billing-wallet/app/handlers.py::release`):
1. *Mismo mensaje entregado dos veces:* la transacción incluye `Put MSG#<consumer>#<message_id>` con
   `attribute_not_exists`; el duplicado cancela la transacción completa → cero efectos.
2. *Compensación reenviada con otro id* (reintento por timeout): la retención ya está en `RELEASED` y
   la condición `#st = :held` impide reembolsar otra vez; se responde `refunded_cop: 0`.
Prueba: `tests/integration/test_saga_e2e.py::test_compensacion_duplicada_es_idempotente`.

## 6.4 Observabilidad y presentación (20 %)

**Prefect + CloudWatch con el correlation_id de una SAGA fallida.**
- Prefect: flow `aegisvolt-observability` (cada 5 min) → subflows `platform-health` y `saga-audit`;
  artefactos con métricas (completadas, compensadas, huérfanas, retenciones colgadas, desfase CQRS).
- CloudWatch Logs Insights: consulta guardada `AegisVolt/<env>/trace-saga-by-correlation-id`
  (Terraform, módulo `observability`). Reemplazar el ID y ver los logs de los 5 servicios en orden.

**Cambiar una flag en dev sin redesplegar y que prod no cambie.** Cada ambiente tiene su tabla
(`aegisvolt-feature-flags-dev` vs `-prod`) y su Lambda; los servicios releen las flags cada 3 s
(`aegis_common/flags.py`). Terraform crea los ítems con `ignore_changes = [item]`, así que un apply
posterior no revierte el cambio. En la UI de dev el efecto aparece en ≤ 3 s; en prod la tabla nunca se tocó.
