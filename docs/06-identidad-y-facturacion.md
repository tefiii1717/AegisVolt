# 06 · Identidad, roles, medios de pago y facturas

## Roles y separación de responsabilidades

El rol sale del dominio del correo y define los scopes OAuth2 del token. Ningún rol tiene las tareas de otro.

| Rol | Correo | Puede | No puede |
|---|---|---|---|
| Operador de flota | `@operador` | Reservar cargas, registrar medios de pago, ver **sus** cargas y facturas | Ver cargas o facturas de otros, flags, usuarios |
| Administrador | `@admin` | Crear cuentas, cambiar feature flags, ver la salud de la plataforma | Reservar, ver cargas o facturas |
| Auditor | `@auditor` | Ver **todas** las transacciones y facturas (solo lectura) | Reservar, cambiar flags, crear cuentas |

Scopes (`gateway/graphql-bff/src/auth.js`, Cognito en `infra/terraform/modules/cognito`):

| Scope | Operador | Admin | Auditor |
|---|:-:|:-:|:-:|
| `aegisvolt/stations.read` | ✔ | ✔ | ✔ |
| `aegisvolt/transactions.read` (propias) | ✔ | | |
| `aegisvolt/reservations.write` | ✔ | | |
| `aegisvolt/payments.manage` | ✔ | | |
| `aegisvolt/audit.read` (todas) | | | ✔ |
| `aegisvolt/flags.admin` | | ✔ | |
| `aegisvolt/users.admin` | | ✔ | |
| `aegisvolt/platform.read` | | ✔ | |

La consola oculta las secciones que el rol no usa, pero la regla real está en el BFF: cada campo GraphQL
exige su scope, y una transacción ajena se responde como inexistente (no se revela que existe).

## Registro e ingreso

- `POST /auth/register`: autorregistro, **solo** correos `@operador`. Un `@admin` o `@auditor` recibe 403.
- `POST /auth/login`: correo y contraseña → access token RS256 de 1 hora con los scopes del rol.
- Las cuentas `@admin` y `@auditor` las crea un administrador (mutación `createUser`, página Usuarios).
- Al arrancar, el BFF asegura un **administrador inicial** (`BOOTSTRAP_ADMIN_EMAIL` + clave en el Secret
  `aegisvolt-identity`). En el clúster: `scripts/floci/deploy-k8s.sh dev admin` muestra sus credenciales.

Seguridad:

- Contraseñas con **scrypt** y sal aleatoria; comparación en tiempo constante; mínimo 8 caracteres con letras y números.
- Mismo mensaje y misma duración para «usuario inexistente» y «clave incorrecta» (no se enumeran correos).
- 5 intentos fallidos por IP y correo en 5 minutos bloquean el ingreso temporalmente.
- La llave RSA de firma vive en un Secret de Kubernetes generado por Helm, compartido por todas las réplicas.
- Este proveedor local existe solo con `AUTH_MODE=dev` (local/dev/test). En AWS real lo reemplaza Cognito.

Los usuarios viven en la tabla `aegisvolt-identity-<env>` (Terraform), que solo el BFF puede tocar (IAM).

## Medios de pago

Un medio de pago es la empresa de flota a la que se facturan las cargas: **nombre de la empresa + número de
cuenta**. Lo registra el operador (`registerPaymentMethod`) y queda a su nombre.

- `billing-wallet` lo guarda como una cuenta de cobro `WALLET#pm-…` con un **cupo** (3.000.000 COP por
  defecto, `PAYMENT_METHOD_CREDIT_LIMIT_COP`). La SAGA retiene y captura contra ese cupo, igual que antes.
- Del número de cuenta solo se guardan los **últimos 4 dígitos** y una huella SHA-256.
- Antes de iniciar la SAGA, el BFF verifica contra `billing-wallet` (fuente de verdad, no la proyección)
  que el medio de pago pertenezca al operador. Usar uno ajeno responde `FORBIDDEN`.

## Facturas

```
reserva → retención en el cupo → activación (pivote) → CAPTURA + FACTURA (misma transacción DynamoDB)
                                       └─ falla → compensaciones → cupo liberado, SIN factura
```

- La factura (`INVOICE#<saga>`) se escribe en la **misma transacción** que la captura, con condición
  `attribute_not_exists`: una captura reintentada o duplicada nunca genera dos facturas.
- Número `FAC-<fecha Bogotá>-<8 caracteres de la SAGA>`, total con IVA del 19 % incluido y desglosado.
- El evento `billing.InvoiceIssued` sale por el outbox; `query-projection` lo proyecta en la partición del
  operador (`OWNER#<usuario>`) y en la vista global del auditor (`INVOICES`).

Pruebas: `tests/integration/test_payments_invoices.py` (factura única, sin factura al compensar, aislamiento
por dueño) y `tests/e2e/test_graphql_e2e.py` (roles, registro, factura de punta a punta en el clúster).
