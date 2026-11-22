"""Persistencia y ejecución de la SAGA: log de SAGA en DynamoDB + outbox de comandos."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis_common import topics
from aegis_common.dynamo import BusinessConditionFailed, Txn, execute, from_ddb, get, now_iso, values
from aegis_common.envelope import Envelope

from .saga import TERMINAL, Decision, SagaState, decide, on_timeout, reserve_cmd

log = logging.getLogger("orchestrator")
SOURCE = "saga-orchestrator"
CONSUMER = "orchestrator"
ACTIVE = "SAGA#ACTIVE"
MAX_RETRIES_BEFORE_STUCK = 5


class StartSagaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fleet_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    station_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9-]+$")
    connector_id: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9-]+$")
    vehicle_id: str = Field(min_length=1, max_length=32)
    estimated_kwh: float = Field(gt=0, le=400)
    fast_charge: bool = False
    requested_by: str | None = Field(default=None, max_length=128)
    requested_by_email: str | None = Field(default=None, max_length=254)
    # Empresa a la que se factura (medio de pago del operador); fleet_id es la cuenta de cobro.
    company_name: str | None = Field(default=None, max_length=80)


def saga_key(saga_id: str) -> dict[str, str]:
    return {"pk": f"SAGA#{saga_id}", "sk": "SAGA"}


class SagaEngine:
    def __init__(self, client, table: str, environment: str, *, step_timeout_s: float | None = None) -> None:
        self.client, self.table, self.environment = client, table, environment
        self.step_timeout_s = step_timeout_s or float(os.getenv("SAGA_STEP_TIMEOUT_S", "30"))

    def _deadline(self) -> str:
        return (datetime.now(UTC) + timedelta(seconds=self.step_timeout_s)).isoformat()

    def _message(self, saga_id: str, type_: str, payload: dict, causation: str | None) -> Envelope:
        return Envelope(type=type_, correlation_id=saga_id, causation_id=causation, source=SOURCE,
                        environment=self.environment, payload=payload)

    @staticmethod
    def snapshot(saga: dict) -> dict[str, Any]:
        keys = ("saga_id", "state", "fleet_id", "vehicle_id", "station_id", "connector_id", "estimated_kwh",
                "fast_charge", "power_kw", "tier", "amount_cop", "surge_applied", "multiplier", "failure_reason",
                "steps", "compensations", "created_at", "updated_at", "version", "requested_by", "stuck",
                "requested_by_email", "company_name")
        return {k: saga.get(k) for k in keys if saga.get(k) is not None}

    # ---------- inicio ----------
    def start(self, req: StartSagaRequest, idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key:
            existing = get(self.client, self.table, f"IDEMP#{idempotency_key}", "IDEMP")
            if existing:
                return self.load(existing["saga_id"]) or {"saga_id": existing["saga_id"]}
        saga_id = str(uuid.uuid4())
        now = now_iso()
        saga = {
            **saga_key(saga_id), "saga_id": saga_id, "state": SagaState.RESERVING.value, "version": 1,
            **req.model_dump(exclude_none=True), "steps": [{"step": "SAGA_STARTED", "status": "SUCCEEDED", "at": now,
                                                            "detail": {}}],
            "compensations": [], "retries": 0, "created_at": now, "updated_at": now,
            "gsi1pk": ACTIVE, "gsi1sk": self._deadline(),
        }
        cmd = reserve_cmd(saga)
        txn = Txn(self.table)
        if idempotency_key:
            txn.put({"pk": f"IDEMP#{idempotency_key}", "sk": "IDEMP", "saga_id": saga_id, "created_at": now},
                    condition="attribute_not_exists(pk)", label="idempotency_key")
        txn.put(saga, condition="attribute_not_exists(pk)", label="saga")
        txn.outbox(cmd.topic, self._message(saga_id, cmd.type, cmd.payload, None))
        txn.outbox(topics.SAGA_EVENTS, self._message(saga_id, "saga.SagaStateChanged", self.snapshot(saga), None))
        try:
            execute(self.client, txn)
        except BusinessConditionFailed:
            existing = get(self.client, self.table, f"IDEMP#{idempotency_key}", "IDEMP")
            return self.load(existing["saga_id"]) if existing else {}
        log.info("SAGA iniciada", extra={"correlation_id": saga_id, "station": req.station_id,
                                         "connector": req.connector_id, "fleet": req.fleet_id})
        return self.snapshot(saga)

    def load(self, saga_id: str) -> dict[str, Any] | None:
        item = get(self.client, self.table, **saga_key(saga_id))
        return self.snapshot(item) if item else None

    # ---------- avance ----------
    def _apply(self, saga: dict, decision: Decision, *, causation: str | None, message_id: str | None,
               is_retry: bool = False) -> None:
        now = now_iso()
        step = {**(decision.step or {}), "at": now}
        new = {**saga, **decision.updates, "state": decision.next_state.value, "updated_at": now,
               "version": saga["version"] + 1, "steps": [*saga.get("steps", []), step]}
        if decision.compensation:
            comp = {**decision.compensation, "at": now}
            new["compensations"] = [*saga.get("compensations", []), comp]
        new["retries"] = saga.get("retries", 0) + 1 if is_retry else 0
        new["stuck"] = new["retries"] >= MAX_RETRIES_BEFORE_STUCK
        terminal = decision.next_state in TERMINAL
        if terminal:
            new.pop("gsi1pk", None)
            new.pop("gsi1sk", None)
        else:
            new["gsi1pk"], new["gsi1sk"] = ACTIVE, self._deadline()

        txn = Txn(self.table).put(new, condition="version = :v", vals=values(v=saga["version"]), label="saga")
        for cmd in decision.commands:
            txn.outbox(cmd.topic, self._message(saga["saga_id"], cmd.type, cmd.payload, causation))
        txn.outbox(topics.SAGA_EVENTS, self._message(saga["saga_id"], "saga.SagaStateChanged", self.snapshot(new),
                                                     causation))
        execute(self.client, txn, message_id=message_id, consumer=CONSUMER)
        log_fn = log.warning if decision.next_state in (SagaState.COMPENSATING_FUNDS,
                                                        SagaState.COMPENSATING_RESERVATION,
                                                        SagaState.COMPENSATED, SagaState.FAILED) else log.info
        log_fn("transición de SAGA", extra={"correlation_id": saga["saga_id"], "from": saga["state"],
                                            "to": decision.next_state.value, "step": step.get("step"),
                                            "step_status": step.get("status")})

    def on_reply(self, env: Envelope) -> None:
        item = get(self.client, self.table, **saga_key(env.correlation_id))
        if item is None:
            log.warning("respuesta para SAGA desconocida", extra={"type": env.type})
            return
        if SagaState(item["state"]) in TERMINAL:
            log.info("respuesta tardía ignorada (SAGA terminada)", extra={"type": env.type, "state": item["state"]})
            return
        decision = decide(item, env.type, env.payload)
        if decision is None:
            log.info("respuesta fuera de secuencia ignorada", extra={"type": env.type, "state": item["state"]})
            return
        try:
            self._apply(item, decision, causation=env.message_id, message_id=env.message_id)
        except BusinessConditionFailed as exc:
            raise RuntimeError("conflicto de versión en la SAGA; se reintentará") from exc

    async def handle(self, env: Envelope) -> None:
        await asyncio.to_thread(self.on_reply, env)

    # ---------- timeouts ----------
    def sweep_timeouts(self, limit: int = 25) -> int:
        resp = self.client.query(TableName=self.table, IndexName="gsi1",
                                 KeyConditionExpression="gsi1pk = :p AND gsi1sk <= :now",
                                 ExpressionAttributeValues=values(p=ACTIVE, now=datetime.now(UTC).isoformat()),
                                 Limit=limit)
        count = 0
        for raw in resp.get("Items", []):
            saga = from_ddb(raw)
            if SagaState(saga["state"]) in TERMINAL:
                continue
            decision = on_timeout(saga)
            retry = decision.next_state.value == saga["state"]
            try:
                self._apply(saga, decision, causation=None, message_id=None, is_retry=retry)
                count += 1
            except BusinessConditionFailed:
                continue  # otra réplica avanzó la SAGA
        return count

    def list_recent(self, limit: int = 20) -> list[dict]:
        resp = self.client.scan(TableName=self.table, FilterExpression="sk = :s", ExpressionAttributeValues=values(s="SAGA"))
        sagas = [self.snapshot(from_ddb(i)) for i in resp.get("Items", [])]
        return sorted(sagas, key=lambda s: s.get("created_at", ""), reverse=True)[:limit]
