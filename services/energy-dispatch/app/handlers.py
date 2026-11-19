"""Paso 3 de la SAGA: activación física del cargador = TRANSACCIÓN PIVOTE.

Antes de este paso todo es compensable; una vez que fluye energía no se puede "deshacer", así que
lo que sigue (CaptureHold) solo se reintenta. La flag SIMULATE_CHARGER_HARDWARE_FAULT inyecta un
fallo físico para demostrar el rollback distribuido.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from aegis_common import topics
from aegis_common.circuit_breaker import CircuitBreaker, CircuitOpenError
from aegis_common.dynamo import (
    BusinessConditionFailed,
    Txn,
    already_processed,
    execute,
    from_ddb,
    get,
    now_iso,
    ttl_epoch,
    values,
)
from aegis_common.envelope import Envelope

from .charger import ChargerGateway
from .domain import CancelCharging, MeterValues, SessionStatus, StartCharging, pointer_key, session_key

log = logging.getLogger("dispatch")
SOURCE = "energy-dispatch-service"
CONSUMER = "dispatch"
ACTIVE_INDEX = "SESSION#ACTIVE"


class DispatchHandlers:
    def __init__(self, client, table: str, flags, charger: ChargerGateway, breaker: CircuitBreaker, *,
                 time_scale: float | None = None, environment: str = "local") -> None:
        self.client, self.table, self.flags = client, table, flags
        self.charger, self.breaker, self.environment = charger, breaker, environment
        # Factor de compresión del tiempo para demos: 60 → una hora de carga dura un minuto.
        self.time_scale = time_scale or float(os.getenv("SESSION_TIME_SCALE", "60"))

    async def handle(self, env: Envelope) -> None:
        if env.type == "dispatch.StartCharging":
            await self.start(env)
        elif env.type == "dispatch.CancelCharging":
            await self.cancel(env)
        elif env.type == "telemetry.MeterValuesRecorded":
            await asyncio.to_thread(self.record_telemetry, env)

    def _event(self, saga_id: str, type_: str, payload: dict, causation: Envelope) -> Envelope:
        return Envelope(type=type_, correlation_id=saga_id, causation_id=causation.message_id, source=SOURCE,
                        environment=causation.environment, payload=payload)

    def _fail_txn(self, env: Envelope, cmd: StartCharging, reason: str, **extra) -> Txn:
        reply = env.reply("dispatch.ChargingFailed", {"reason": reason, "station_id": cmd.station_id,
                                                      "connector_id": cmd.connector_id, **extra}, source=SOURCE)
        return (Txn(self.table)
                .put({**session_key(cmd.saga_id), "status": SessionStatus.FAILED.value, "station_id": cmd.station_id,
                      "connector_id": cmd.connector_id, "failure_reason": reason, "created_at": now_iso(), **extra},
                     condition="attribute_not_exists(pk)", label="session")
                .outbox(topics.SAGA_REPLIES, reply))

    async def start(self, env: Envelope) -> None:
        cmd = StartCharging.model_validate(env.payload)
        # Evitar efectos físicos duplicados: si el mensaje ya se procesó no se vuelve a llamar al hardware.
        if await asyncio.to_thread(already_processed, self.client, self.table, env.message_id, CONSUMER):
            return
        existing = await asyncio.to_thread(get, self.client, self.table, **session_key(cmd.saga_id))
        if existing:
            if existing["status"] == SessionStatus.ACTIVE.value:
                reply = env.reply("dispatch.ChargingStarted", {"session_id": cmd.saga_id,
                                                               "legacy_transaction_id": existing.get("legacy_transaction_id")},
                                  source=SOURCE)
                await asyncio.to_thread(execute, self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, reply),
                                        message_id=env.message_id, consumer=CONSUMER)
            else:
                reply = env.reply("dispatch.ChargingFailed", {"reason": f"SESSION_{existing['status']}"}, source=SOURCE)
                await asyncio.to_thread(execute, self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, reply),
                                        message_id=env.message_id, consumer=CONSUMER)
            return

        if self.flags.is_enabled("SIMULATE_CHARGER_HARDWARE_FAULT"):
            log.error("FALLA FÍSICA SIMULADA en el cargador (flag activa)",
                      extra={"station": cmd.station_id, "connector": cmd.connector_id, "ocpp_error": "GroundFailure"})
            txn = self._fail_txn(env, cmd, "HARDWARE_FAULT_SIMULATED", ocpp_error_code="GroundFailure")
            await asyncio.to_thread(execute, self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            return

        try:
            result = await self.breaker.call(self.charger.remote_start, station_id=cmd.station_id,
                                             connector_id=cmd.connector_id, power_kw=cmd.power_kw,
                                             idempotency_key=cmd.saga_id)
        except CircuitOpenError:
            txn = self._fail_txn(env, cmd, "CHARGER_UNREACHABLE_CIRCUIT_OPEN")
            await asyncio.to_thread(execute, self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            return
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del hardware es fallo del paso 3
            log.error("el cargador rechazó o no respondió", extra={"error": str(exc)})
            txn = self._fail_txn(env, cmd, "CHARGER_ERROR", detail=str(exc)[:200])
            await asyncio.to_thread(execute, self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            return

        hours = cmd.estimated_kwh / cmd.power_kw
        ends_at = (datetime.now(UTC) + timedelta(seconds=max(30.0, hours * 3600 / self.time_scale))).isoformat()
        tx_id = result.get("transactionId")
        started = self._event(cmd.saga_id, "dispatch.SessionStarted", {
            "session_id": cmd.saga_id, "station_id": cmd.station_id, "connector_id": cmd.connector_id,
            "power_kw": cmd.power_kw, "legacy_transaction_id": tx_id, "ends_at": ends_at}, env)
        txn = (Txn(self.table)
               .put({**session_key(cmd.saga_id), "status": SessionStatus.ACTIVE.value, "station_id": cmd.station_id,
                     "connector_id": cmd.connector_id, "power_kw": cmd.power_kw, "estimated_kwh": cmd.estimated_kwh,
                     "energy_kwh": 0, "legacy_transaction_id": tx_id, "started_at": now_iso(), "ends_at": ends_at,
                     "gsi1pk": ACTIVE_INDEX, "gsi1sk": ends_at},
                    condition="attribute_not_exists(pk)", label="session")
               .put({**pointer_key(cmd.station_id, cmd.connector_id), "saga_id": cmd.saga_id}, label="pointer")
               .outbox(topics.SAGA_REPLIES, env.reply("dispatch.ChargingStarted", {
                   "session_id": cmd.saga_id, "legacy_transaction_id": tx_id}, source=SOURCE))
               .outbox(topics.DISPATCH_EVENTS, started))
        try:
            await asyncio.to_thread(execute, self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            log.info("energía fluyendo: transacción pivote completada", extra={"legacy_tx": tx_id})
        except BusinessConditionFailed:
            # Llegó una cancelación (lápida) mientras se activaba: se detiene el cargador.
            await self.charger.remote_stop(station_id=cmd.station_id, connector_id=cmd.connector_id,
                                           idempotency_key=f"{cmd.saga_id}:stop")
            reply = env.reply("dispatch.ChargingFailed", {"reason": "SESSION_CANCELLED"}, source=SOURCE)
            await asyncio.to_thread(execute, self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, reply),
                                    message_id=env.message_id, consumer=CONSUMER)

    async def cancel(self, env: Envelope) -> None:
        cmd = CancelCharging.model_validate(env.payload)
        session = await asyncio.to_thread(get, self.client, self.table, **session_key(cmd.saga_id))
        if session is None:
            txn = Txn(self.table).put({**session_key(cmd.saga_id), "status": SessionStatus.CANCELLED.value,
                                       "created_at": now_iso(), "failure_reason": cmd.reason},
                                      condition="attribute_not_exists(pk)", label="session")
            try:
                await asyncio.to_thread(execute, self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                return
            except BusinessConditionFailed:
                session = await asyncio.to_thread(get, self.client, self.table, **session_key(cmd.saga_id))
        if session and session["status"] == SessionStatus.ACTIVE.value:
            await self.end_session(session, reason=cmd.reason, causation=env)

    async def end_session(self, session: dict, *, reason: str, causation: Envelope | None = None) -> None:
        saga_id = session["pk"].split("#", 1)[1]
        try:
            await self.breaker.call(self.charger.remote_stop, station_id=session["station_id"],
                                    connector_id=session["connector_id"], idempotency_key=f"{saga_id}:stop")
        except Exception:  # noqa: BLE001 - el cierre lógico se registra aunque el legado no responda
            log.warning("remote-stop falló; la sesión se cierra lógicamente", exc_info=True)
        cause = causation or Envelope(type="dispatch.SessionWatchdog", correlation_id=saga_id, source=SOURCE,
                                      environment=self.environment)
        ended = self._event(saga_id, "dispatch.SessionEnded", {
            "session_id": saga_id, "station_id": session["station_id"], "connector_id": session["connector_id"],
            "energy_kwh": session.get("energy_kwh", 0), "reason": reason}, cause)
        txn = (Txn(self.table)
               .update(session_key(saga_id), "SET #st = :ended, ended_at = :now, end_reason = :r REMOVE gsi1pk, gsi1sk",
                       condition="#st = :active", names={"#st": "status"},
                       vals=values(ended=SessionStatus.ENDED.value, active=SessionStatus.ACTIVE.value, now=now_iso(),
                                   r=reason), label="session")
               .outbox(topics.DISPATCH_EVENTS, ended))
        try:
            await asyncio.to_thread(execute, self.client, txn)
            log.info("sesión de carga finalizada", extra={"correlation_id": saga_id, "reason": reason})
        except BusinessConditionFailed:
            pass

    def record_telemetry(self, env: Envelope) -> None:
        mv = MeterValues.model_validate(env.payload)
        pointer = get(self.client, self.table, **pointer_key(mv.station_id, mv.connector_id))
        if not pointer:
            return
        saga_id = pointer["saga_id"]
        txn = (Txn(self.table)
               .put({"pk": f"SESSION#{saga_id}", "sk": f"TELEMETRY#{mv.measured_at}", "energy_kwh": mv.energy_kwh,
                     "power_kw": mv.power_kw, "soc_percent": mv.soc_percent, "ttl": ttl_epoch(3)}, label="sample")
               .update(session_key(saga_id), "SET energy_kwh = :e, last_power_kw = :p, soc_percent = :s, last_sample_at = :t",
                       condition="#st = :active", names={"#st": "status"},
                       vals=values(e=mv.energy_kwh, p=mv.power_kw, s=mv.soc_percent, t=mv.measured_at,
                                   active=SessionStatus.ACTIVE.value), label="session"))
        try:
            execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
        except BusinessConditionFailed:
            return

    async def watchdog(self) -> int:
        """Cierra sesiones cuyo `ends_at` ya pasó (energía estimada entregada)."""
        now = datetime.now(UTC).isoformat()
        resp = await asyncio.to_thread(self.client.query, TableName=self.table, IndexName="gsi1",
                                       KeyConditionExpression="gsi1pk = :p AND gsi1sk <= :now",
                                       ExpressionAttributeValues=values(p=ACTIVE_INDEX, now=now), Limit=25)
        due = [from_ddb(i) for i in resp.get("Items", [])]
        for session in due:
            await self.end_session(session, reason="ENERGY_DELIVERED")
        return len(due)
