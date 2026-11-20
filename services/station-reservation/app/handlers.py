"""Manejadores de comandos del contexto de reservas.

Cada comando se procesa con UNA transacción DynamoDB que incluye:
  1) registro de idempotencia MSG#<message_id> (attribute_not_exists)
  2) cambio de estado del agregado (con condición de negocio)
  3) respuesta para el orquestador + evento de dominio en el OUTBOX
"""

from __future__ import annotations

import asyncio
import logging

from aegis_common import topics
from aegis_common.dynamo import BusinessConditionFailed, Txn, execute, get, now_iso, values
from aegis_common.envelope import Envelope

from .domain import (
    FAILURE_REASONS,
    ConnectorStatus,
    ReleaseConnector,
    ReservationStatus,
    ReserveConnector,
    Tier,
    connector_key,
    reservation_key,
)

log = logging.getLogger("reservation")
SOURCE = "station-reservation-service"
CONSUMER = "reservation"


class ReservationHandlers:
    def __init__(self, client, table: str, flags) -> None:
        self.client, self.table, self.flags = client, table, flags

    async def handle(self, env: Envelope) -> None:
        route = {
            "reservation.ReserveConnector": self.reserve,
            "reservation.ReleaseConnector": self.release,
            "dispatch.SessionStarted": self.on_session_started,
            "dispatch.SessionEnded": self.on_session_ended,
        }.get(env.type)
        if route is None:
            return
        await asyncio.to_thread(route, env)

    # ---------- helpers ----------
    def _status_event(self, env: Envelope, station_id: str, connector_id: str, status: ConnectorStatus) -> Envelope:
        return env.reply("station.ConnectorStatusChanged", {
            "station_id": station_id, "connector_id": connector_id, "status": status.value,
            "saga_id": env.correlation_id, "changed_at": now_iso(),
        }, source=SOURCE)

    def _fail(self, env: Envelope, reason: str, **extra) -> None:
        reply = env.reply("reservation.ConnectorReservationFailed", {"reason": reason, **extra}, source=SOURCE)
        execute(self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, reply),
                message_id=env.message_id, consumer=CONSUMER)
        log.warning("reserva rechazada", extra={"reason": reason, "correlation_id": env.correlation_id})

    # ---------- Paso 1: ReserveConnector ----------
    def reserve(self, env: Envelope) -> None:
        cmd = ReserveConnector.model_validate(env.payload)
        connector = get(self.client, self.table, **connector_key(cmd.station_id, cmd.connector_id))
        if connector is None:
            return self._fail(env, "CONNECTOR_NOT_FOUND")
        if connector.get("tier") == Tier.ULTRA.value and not self.flags.is_enabled("ENABLE_FAST_CHARGE_BOOST"):
            return self._fail(env, "FAST_CHARGE_DISABLED")

        reply = env.reply("reservation.ConnectorReserved", {
            "reservation_id": cmd.saga_id,
            "station_id": cmd.station_id,
            "connector_id": cmd.connector_id,
            "power_kw": connector["power_kw"],
            "tier": connector.get("tier", Tier.FAST.value),
        }, source=SOURCE)
        txn = (
            Txn(self.table)
            .update(connector_key(cmd.station_id, cmd.connector_id),
                    "SET #st = :reserved, current_saga_id = :sid, updated_at = :now ADD version :one",
                    condition="#st = :available", label="connector",
                    names={"#st": "status"},
                    vals=values(reserved=ConnectorStatus.RESERVED.value, available=ConnectorStatus.AVAILABLE.value,
                                sid=cmd.saga_id, now=now_iso(), one=1))
            .put({**reservation_key(cmd.saga_id), "status": ReservationStatus.ACTIVE.value,
                  "station_id": cmd.station_id, "connector_id": cmd.connector_id, "fleet_id": cmd.fleet_id,
                  "created_at": now_iso()},
                 condition="attribute_not_exists(pk)", label="reservation")
            .outbox(topics.SAGA_REPLIES, reply)
            .outbox(topics.STATION_EVENTS, self._status_event(env, cmd.station_id, cmd.connector_id,
                                                              ConnectorStatus.RESERVED),
                    key=f"{cmd.station_id}#{cmd.connector_id}")
        )
        try:
            outcome = execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            log.info("conector reservado", extra={"outcome": outcome.value, "station": cmd.station_id,
                                                  "connector": cmd.connector_id})
        except BusinessConditionFailed as exc:
            reason = FAILURE_REASONS.get(exc.labels[0], "CONDITION_FAILED")
            self._fail(env, reason, current_status=connector.get("status"))

    # ---------- Compensación: ReleaseConnector ----------
    def release(self, env: Envelope) -> None:
        cmd = ReleaseConnector.model_validate(env.payload)
        reservation = get(self.client, self.table, **reservation_key(cmd.saga_id))
        reply_payload = {"saga_id": cmd.saga_id, "reason": cmd.reason}

        if reservation is None:
            # Compensación antes que la acción → lápida. Si el ReserveConnector llega tarde, su
            # Put con attribute_not_exists fallará y responderá SAGA_ALREADY_RELEASED.
            reply = env.reply("reservation.ConnectorReleased", {**reply_payload, "released": False}, source=SOURCE)
            txn = (Txn(self.table)
                   .put({**reservation_key(cmd.saga_id), "status": ReservationStatus.RELEASED_EMPTY.value,
                         "created_at": now_iso()}, condition="attribute_not_exists(pk)", label="tombstone")
                   .outbox(topics.SAGA_REPLIES, reply))
            try:
                execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
            except BusinessConditionFailed:
                return self.release(env)  # carrera: la reserva apareció; se reintenta la ruta normal
            return

        if reservation["status"] in (ReservationStatus.RELEASED.value, ReservationStatus.RELEASED_EMPTY.value):
            # Idempotencia a nivel de dominio: compensación repetida con otro message_id.
            reply = env.reply("reservation.ConnectorReleased", {**reply_payload, "released": False}, source=SOURCE)
            execute(self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, reply),
                    message_id=env.message_id, consumer=CONSUMER)
            return

        sid, cid = reservation["station_id"], reservation["connector_id"]
        reply = env.reply("reservation.ConnectorReleased", {**reply_payload, "released": True,
                                                             "station_id": sid, "connector_id": cid}, source=SOURCE)
        base = (Txn(self.table)
                .update(reservation_key(cmd.saga_id), "SET #st = :released, released_at = :now",
                        names={"#st": "status"}, vals=values(released=ReservationStatus.RELEASED.value, now=now_iso()),
                        label="reservation")
                .outbox(topics.SAGA_REPLIES, reply))
        txn = (Txn(self.table, list(base.ops), list(base.labels))
               .update(connector_key(sid, cid), "SET #st = :available, updated_at = :now REMOVE current_saga_id ADD version :one",
                       condition="current_saga_id = :sid", label="connector", names={"#st": "status"},
                       vals=values(available=ConnectorStatus.AVAILABLE.value, now=now_iso(), sid=cmd.saga_id, one=1))
               .outbox(topics.STATION_EVENTS, self._status_event(env, sid, cid, ConnectorStatus.AVAILABLE),
                       key=f"{sid}#{cid}"))
        try:
            execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
        except BusinessConditionFailed:
            # El conector ya no pertenece a esta SAGA: solo se cierra la reserva.
            execute(self.client, base, message_id=env.message_id, consumer=CONSUMER)
        log.info("conector liberado (compensación)", extra={"station": sid, "connector": cid})

    # ---------- Reacciones a eventos de energy-dispatch (coreografía) ----------
    def _set_connector_for_session(self, env: Envelope, new_status: ConnectorStatus, res_status: ReservationStatus) -> None:
        saga_id = env.correlation_id
        reservation = get(self.client, self.table, **reservation_key(saga_id))
        if not reservation or reservation["status"] not in (ReservationStatus.ACTIVE.value, ReservationStatus.CHARGING.value):
            return
        sid, cid = reservation["station_id"], reservation["connector_id"]
        txn = (Txn(self.table)
               .update(connector_key(sid, cid),
                       "SET #st = :new, updated_at = :now ADD version :one" + (" REMOVE current_saga_id"
                                                                              if new_status is ConnectorStatus.AVAILABLE else ""),
                       condition="current_saga_id = :sid", names={"#st": "status"},
                       vals=values(new=new_status.value, now=now_iso(), sid=saga_id, one=1), label="connector")
               .update(reservation_key(saga_id), "SET #st = :rs", names={"#st": "status"},
                       vals=values(rs=res_status.value), label="reservation")
               .outbox(topics.STATION_EVENTS, self._status_event(env, sid, cid, new_status), key=f"{sid}#{cid}"))
        try:
            execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
        except BusinessConditionFailed:
            log.info("evento de sesión ignorado: el conector ya no pertenece a la SAGA")

    def on_session_started(self, env: Envelope) -> None:
        self._set_connector_for_session(env, ConnectorStatus.CHARGING, ReservationStatus.CHARGING)

    def on_session_ended(self, env: Envelope) -> None:
        self._set_connector_for_session(env, ConnectorStatus.AVAILABLE, ReservationStatus.FINISHED)
