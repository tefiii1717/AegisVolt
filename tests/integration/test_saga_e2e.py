"""La SAGA "Reservar y Activar Carga" de punta a punta, con fallos inducidos.

Estos escenarios son exactamente las demostraciones en vivo de la sustentación (§6.3).
"""

import pytest

from aegis_common import topics
from aegis_common.envelope import Envelope

pytestmark = pytest.mark.asyncio


async def test_happy_path_completa_y_captura(platform):
    saga = platform.start()
    await platform.pump()

    final = platform.engine.load(saga["saga_id"])
    assert final["state"] == "COMPLETED"
    assert [s["step"] for s in final["steps"]] == [
        "SAGA_STARTED", "RESERVE_CONNECTOR", "AUTHORIZE_FUNDS", "START_CHARGING", "CAPTURE_FUNDS"]
    wallet = platform.item("billing", "WALLET#flota-andina", "WALLET")
    assert wallet["captured_cop"] == 40_000 and wallet["held_cop"] == 0
    assert wallet["available_cop"] == 2_500_000 - 40_000
    connector = platform.item("reservations", "STATION#EST-BOG-080", "CONNECTOR#C1")
    assert connector["status"] == "CHARGING"  # reacción coreografiada a dispatch.SessionStarted
    # CQRS: el modelo de lectura converge
    projected = platform.projector.saga(saga["saga_id"])
    assert projected["state"] == "COMPLETED"
    station = platform.projector.station("EST-BOG-080")
    assert next(c for c in station["connectors"] if c["connector_id"] == "C1")["status"] == "CHARGING"


async def test_fallo_fisico_simulado_reembolsa_y_libera(platform):
    platform.flags.flags["SIMULATE_CHARGER_HARDWARE_FAULT"] = True
    saga = platform.start()
    await platform.pump()

    final = platform.engine.load(saga["saga_id"])
    assert final["state"] == "COMPENSATED"
    assert final["failure_reason"] == "HARDWARE_FAULT_SIMULATED"
    assert [c["action"] for c in final["compensations"] if c["status"] == "DONE"] == [
        "REFUND_FUNDS", "RELEASE_CONNECTOR"]  # orden inverso: primero billing, luego reservas
    wallet = platform.item("billing", "WALLET#flota-andina", "WALLET")
    assert wallet["available_cop"] == 2_500_000 and wallet["held_cop"] == 0
    hold = platform.item("billing", f"HOLD#{saga['saga_id']}", "HOLD")
    assert hold["status"] == "RELEASED"
    assert platform.item("reservations", "STATION#EST-BOG-080", "CONNECTOR#C1")["status"] == "AVAILABLE"
    assert platform.charger.started == []  # el hardware nunca se activó


async def test_fondos_insuficientes_libera_conector(platform):
    saga = platform.start(fleet_id="flota-cafe-express", estimated_kwh=80)
    await platform.pump()

    final = platform.engine.load(saga["saga_id"])
    assert final["state"] == "COMPENSATED"
    assert final["failure_reason"] == "INSUFFICIENT_FUNDS"
    assert platform.item("reservations", "STATION#EST-BOG-080", "CONNECTOR#C1")["status"] == "AVAILABLE"
    assert platform.item("billing", "WALLET#flota-cafe-express", "WALLET")["available_cop"] == 40_000


async def test_conector_ocupado_falla_sin_compensar(platform):
    first = platform.start()
    await platform.pump()
    second = platform.start(fleet_id="flota-sabana-verde")
    await platform.pump()
    assert platform.engine.load(first["saga_id"])["state"] == "COMPLETED"
    final = platform.engine.load(second["saga_id"])
    assert final["state"] == "FAILED" and final["failure_reason"] == "CONNECTOR_NOT_AVAILABLE"


async def test_ultra_bloqueado_sin_flag_fast_charge(platform):
    platform.flags.flags["ENABLE_FAST_CHARGE_BOOST"] = False
    saga = platform.start(connector_id="C2")
    await platform.pump()
    final = platform.engine.load(saga["saga_id"])
    assert final["state"] == "FAILED" and final["failure_reason"] == "FAST_CHARGE_DISABLED"


async def test_circuit_breaker_abre_y_la_saga_compensa_rapido(platform):
    platform.charger.fail = True
    s1 = platform.start(connector_id="C1")
    s2 = platform.start(station_id="EST-FON-002", connector_id="C1")
    s3 = platform.start(station_id="EST-FON-002", connector_id="C2")
    await platform.pump()
    reasons = [platform.engine.load(s["saga_id"])["failure_reason"] for s in (s1, s2, s3)]
    assert reasons[:2] == ["CHARGER_ERROR", "CHARGER_ERROR"]
    assert reasons[2] == "CHARGER_UNREACHABLE_CIRCUIT_OPEN"  # fail-fast: no se llamó al legado
    assert platform.breaker.state.value == "OPEN"
    assert all(platform.engine.load(s["saga_id"])["state"] == "COMPENSATED" for s in (s1, s2, s3))


async def test_compensacion_duplicada_es_idempotente(platform):
    platform.flags.flags["SIMULATE_CHARGER_HARDWARE_FAULT"] = True
    saga = platform.start()
    await platform.pump()
    refund_cmd = next(m for m in platform.bus.messages(topics.BILLING_COMMANDS) if m.type == "billing.ReleaseHold")

    # 1) Mismo message_id reentregado (at-least-once de Kafka): no tiene ningún efecto.
    await platform.billing.handle(refund_cmd)
    # 2) Compensación repetida con OTRO message_id (reintento del orquestador): idempotencia de dominio.
    retry = Envelope(**{**refund_cmd.model_dump(), "message_id": "otro-id"})
    await platform.billing.handle(retry)
    await platform.pump()

    wallet = platform.item("billing", "WALLET#flota-andina", "WALLET")
    assert wallet["available_cop"] == 2_500_000  # nunca se reembolsa dos veces
    ledger = [m for m in platform.bus.messages(topics.SAGA_REPLIES) if m.type == "billing.FundsRefunded"]
    assert [m.payload["refunded_cop"] for m in ledger] == [40_000, 0]


async def test_compensacion_antes_que_la_accion_deja_lapida(platform):
    """Mensaje tardío: llega ReleaseHold antes que AuthorizeHold → la retención posterior se rechaza."""
    saga_id = "saga-fantasma"
    base = dict(correlation_id=saga_id, source="test", environment="local")
    await platform.billing.handle(Envelope(type="billing.ReleaseHold", payload={"saga_id": saga_id}, **base))
    await platform.billing.handle(Envelope(type="billing.AuthorizeHold", payload={
        "saga_id": saga_id, "fleet_id": "flota-andina", "estimated_kwh": 10}, **base))
    replies = [m for m in platform.bus.messages(topics.SAGA_REPLIES)] or []
    await platform.relays[1].run_once()
    replies = [m for m in platform.bus.messages(topics.SAGA_REPLIES)]
    assert replies[-1].type == "billing.FundsHoldRejected"
    assert replies[-1].payload["reason"] == "SAGA_ALREADY_RELEASED"
    assert platform.item("billing", "WALLET#flota-andina", "WALLET")["held_cop"] == 0


async def test_idempotency_key_no_duplica_sagas(platform):
    a = platform.start(idempotency_key="click-1")
    b = platform.start(idempotency_key="click-1")
    assert a["saga_id"] == b["saga_id"]


async def test_timeout_en_pivote_cancela_y_reembolsa(platform):
    saga = platform.start()
    # Se entrega todo menos los comandos de dispatch (el cargador "nunca responde").
    platform.bus.subscribers[topics.DISPATCH_COMMANDS].clear()
    await platform.pump()
    assert platform.engine.load(saga["saga_id"])["state"] == "STARTING_CHARGE"
    platform.engine.step_timeout_s = -1  # todo vence ya
    item = platform.item("sagas", f"SAGA#{saga['saga_id']}", "SAGA")
    platform.client.update_item(TableName=platform.t["sagas"], Key={"pk": {"S": item["pk"]}, "sk": {"S": "SAGA"}},
                                UpdateExpression="SET gsi1sk = :d", ExpressionAttributeValues={":d": {"S": "2000-01-01"}})
    assert platform.engine.sweep_timeouts() == 1
    await platform.pump()
    final = platform.engine.load(saga["saga_id"])
    assert final["state"] == "COMPENSATED" and final["failure_reason"] == "CHARGER_TIMEOUT"
    cancel = [m for m in platform.bus.messages(topics.DISPATCH_COMMANDS) if m.type == "dispatch.CancelCharging"]
    assert len(cancel) == 1
