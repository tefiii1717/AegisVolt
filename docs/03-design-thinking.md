# 03 · Design Thinking

## 1. Empatizar

| Persona | Contexto | Dolor principal |
|---|---|---|
| **Marcela, coordinadora de flota** (Transportes Andinos, 40 furgones eléctricos de última milla) | Planea turnos de carga entre rutas en Bogotá, Cota y Chía | Llega un furgón a la electrolinera y la bahía está ocupada o averiada; pierde la ventana de entregas |
| **Andrés, administrador de electrolinera** (Hub Siberia) | Opera 4 cargadores rápidos con un sistema de gestión antiguo | Cobros que quedan "colgados" cuando un cargador falla a mitad de la activación |
| **Equipo de plataforma** | Despliega software en estaciones activas | Cada despliegue es un riesgo: no hay forma de probar una versión con poco tráfico |

Hallazgos (entrevistas simuladas y revisión del caso): el tiempo muerto del vehículo cuesta más que la
energía; la confianza en el cobro es tan importante como la disponibilidad; el sistema heredado no se
puede apagar de un día para otro.

## 2. Definir

**Punto de vista:** Marcela necesita **asegurar una bahía y saber al instante si la carga quedó activa**,
porque cada minuto de un furgón detenido retrasa entregas; si algo falla, necesita **recuperar su saldo
y la bahía sin llamar a nadie**.

**¿Cómo podríamos…?**
- …reservar una bahía y retener saldo en un solo gesto, sin cobros fantasma si el cargador falla?
- …mostrar en vivo qué conectores están libres, reservados o cargando?
- …probar funcionalidades nuevas (tarifa dinámica, ultra-carga) en un ambiente sin afectar producción?
- …migrar del sistema heredado sin detener las estaciones?

## 3. Idear → decisiones

| Idea | Se convirtió en |
|---|---|
| "Un botón: reservar y cargar" | Mutación `reserveAndCharge` que dispara la SAGA |
| "Si falla, que se deshaga solo" | Compensaciones automáticas (reembolso + liberar bahía) visibles en la línea de tiempo |
| "Ver el mapa como en la app de viajes" | Mapa ilustrado de la Sabana con pines por estado (Uiverse) |
| "Encender funciones por ambiente" | Feature Flags por tabla y ambiente, cambiables sin redesplegar |
| "Cambiar el sistema viejo por partes" | Strangler Fig + capas anticorrupción |
| "Desplegar sin miedo" | Canary 20 % con análisis de CloudWatch y rollback automático |

## 4. Prototipar

- UI: inspirada en las referencias (portada Colombiabus, carretera ilustrada, apps de viaje):
  fondo menta, navy andino, CTA naranja; componentes de Uiverse adaptados (mapa con pines, checkout,
  batería cargando, aviso de éxito, alerta de compensación, camión de reparto).
- Backend: SAGA ejecutable en proceso (moto + bus en memoria) antes de tener la nube: permitió validar
  los 10 escenarios de fallo en minutos.

## 5. Probar

| Escenario | Resultado esperado | Prueba |
|---|---|---|
| Reserva normal | Carga activa y cobro confirmado | `test_happy_path_completa_y_captura`, e2e |
| Falla física simulada | Reembolso + bahía libre, sin activar hardware | `test_fallo_fisico_simulado_reembolsa_y_libera`, e2e |
| Flota sin saldo | Bahía liberada de inmediato | `test_fondos_insuficientes_libera_conector` |
| Legado caído | Circuito abierto, compensación inmediata | `test_circuit_breaker_abre_y_la_saga_compensa_rapido` |
| Doble clic / reintento | Una sola SAGA, un solo reembolso | `test_idempotency_key_no_duplica_sagas`, `test_compensacion_duplicada_es_idempotente` |

Siguiente iteración: sesión con usuarios reales para validar la terminología ("retención", "bahía") y
medir el tiempo hasta confirmar una carga.
