"""Paso 2 de la SAGA (retención de fondos) y sus compensaciones.

Modelo Hold → Capture:
  AuthorizeHold : available -= monto, held += monto   (Paso 2, compensable)
  ReleaseHold   : available += monto, held -= monto   (compensación = "reembolso")
  CaptureHold   : held -= monto, captured += monto    (después del pivote, solo reintentable)

Concurrencia: control optimista con `version` en la billetera (varias SAGAs de la misma flota).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from aegis_common import topics
from aegis_common.dynamo import BusinessConditionFailed, Txn, execute, get, now_iso, values
from aegis_common.envelope import Envelope

from .domain import AuthorizeHold, HoldStatus, SagaRef, hold_key, quote, wallet_key

log = logging.getLogger("billing")
SOURCE = "billing-wallet-service"
CONSUMER = "billing"
MAX_OCC_RETRIES = 5


class BillingHandlers:
    def __init__(self, client, table: str, flags, *, tariff_cop_per_kwh: int | None = None,
                 force_peak: bool | None = None, clock=lambda: datetime.now(UTC)) -> None:
        self.client, self.table, self.flags, self.clock = client, table, flags, clock
        self.tariff = tariff_cop_per_kwh or int(os.getenv("BASE_TARIFF_COP_PER_KWH", "1150"))
        self.force_peak = force_peak if force_peak is not None else os.getenv("SURGE_FORCE_PEAK", "false") == "true"

    async def handle(self, env: Envelope) -> None:
        route = {
            "billing.AuthorizeHold": self.authorize,
            "billing.ReleaseHold": self.release,
            "billing.CaptureHold": self.capture,
        }.get(env.type)
        if route:
            await asyncio.to_thread(route, env)

    # ---------- utilidades ----------
    def _reply_only(self, env: Envelope, type_: str, payload: dict) -> None:
        execute(self.client, Txn(self.table).outbox(topics.SAGA_REPLIES, env.reply(type_, payload, source=SOURCE)),
                message_id=env.message_id, consumer=CONSUMER)

    def _wallet_txn(self, env: Envelope, wallet: dict, d_available: int, d_held: int, d_captured: int,
                    *, require_funds: int = 0) -> Txn:
        new = {
            "available_cop": wallet["available_cop"] + d_available,
            "held_cop": wallet["held_cop"] + d_held,
            "captured_cop": wallet.get("captured_cop", 0) + d_captured,
        }
        event = env.reply("billing.WalletBalanceChanged", {"fleet_id": wallet["fleet_id"], **new,
                                                          "version": wallet["version"] + 1, "changed_at": now_iso()},
                          source=SOURCE)
        cond = "version = :v" + (" AND available_cop >= :need" if require_funds else "")
        vals = values(a=new["available_cop"], h=new["held_cop"], c=new["captured_cop"], v=wallet["version"],
                      nv=wallet["version"] + 1, now=now_iso())
        if require_funds:
            vals.update(values(need=require_funds))
        return (Txn(self.table)
                .update(wallet_key(wallet["fleet_id"]),
                        "SET available_cop = :a, held_cop = :h, captured_cop = :c, version = :nv, updated_at = :now",
                        condition=cond, vals=vals, label="wallet")
                .outbox(topics.BILLING_EVENTS, event, key=wallet["fleet_id"]))

    def _ledger(self, txn: Txn, fleet_id: str, saga_id: str, kind: str, amount: int) -> Txn:
        ts = now_iso()
        return txn.put({"pk": f"WALLET#{fleet_id}", "sk": f"LEDGER#{ts}#{saga_id}#{kind}", "kind": kind,
                        "saga_id": saga_id, "amount_cop": amount, "created_at": ts}, label="ledger")

    # ---------- Paso 2 ----------
    def authorize(self, env: Envelope) -> None:
        cmd = AuthorizeHold.model_validate(env.payload)
        q = quote(cmd.estimated_kwh, self.tariff, tier=cmd.tier,
                  surge_enabled=self.flags.is_enabled("ENABLE_DYNAMIC_SURGE_PRICING"),
                  now=self.clock(), force_peak=self.force_peak)
        amount = q["amount_cop"]
        for _ in range(MAX_OCC_RETRIES):
            wallet = get(self.client, self.table, **wallet_key(cmd.fleet_id))
            if wallet is None:
                return self._reply_only(env, "billing.FundsHoldRejected", {"reason": "WALLET_NOT_FOUND", **q})
            if wallet["available_cop"] < amount:
                return self._reply_only(env, "billing.FundsHoldRejected", {
                    "reason": "INSUFFICIENT_FUNDS", "required_cop": amount,
                    "available_cop": wallet["available_cop"], **q})
            reply = env.reply("billing.FundsHeld", {"hold_id": cmd.saga_id, "fleet_id": cmd.fleet_id, **q},
                              source=SOURCE)
            txn = self._wallet_txn(env, wallet, -amount, +amount, 0, require_funds=amount)
            txn.put({**hold_key(cmd.saga_id), "status": HoldStatus.HELD.value, "fleet_id": cmd.fleet_id,
                     "amount_cop": amount, "quote": q, "created_at": now_iso()},
                    condition="attribute_not_exists(pk)", label="hold")
            self._ledger(txn, cmd.fleet_id, cmd.saga_id, "HOLD", amount)
            txn.outbox(topics.SAGA_REPLIES, reply)
            try:
                execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                log.info("fondos retenidos", extra={"fleet": cmd.fleet_id, "amount_cop": amount,
                                                   "surge": q["surge_applied"]})
                return
            except BusinessConditionFailed as exc:
                if "hold" in exc.labels:
                    return self._reply_only(env, "billing.FundsHoldRejected", {"reason": "SAGA_ALREADY_RELEASED", **q})
                continue  # conflicto de versión → releer y reintentar
        self._reply_only(env, "billing.FundsHoldRejected", {"reason": "CONCURRENCY_CONFLICT", **q})

    # ---------- Compensación (reembolso) ----------
    def release(self, env: Envelope) -> None:
        cmd = SagaRef.model_validate(env.payload)
        for _ in range(MAX_OCC_RETRIES):
            hold = get(self.client, self.table, **hold_key(cmd.saga_id))
            if hold is None:
                reply = env.reply("billing.FundsRefunded", {"saga_id": cmd.saga_id, "refunded_cop": 0}, source=SOURCE)
                txn = (Txn(self.table)
                       .put({**hold_key(cmd.saga_id), "status": HoldStatus.RELEASED_EMPTY.value, "created_at": now_iso()},
                            condition="attribute_not_exists(pk)", label="hold")
                       .outbox(topics.SAGA_REPLIES, reply))
                try:
                    execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                    return
                except BusinessConditionFailed:
                    continue
            if hold["status"] != HoldStatus.HELD.value:
                return self._reply_only(env, "billing.FundsRefunded", {
                    "saga_id": cmd.saga_id, "refunded_cop": 0, "hold_status": hold["status"]})
            wallet = get(self.client, self.table, **wallet_key(hold["fleet_id"]))
            amount = hold["amount_cop"]
            txn = self._wallet_txn(env, wallet, +amount, -amount, 0)
            txn.update(hold_key(cmd.saga_id), "SET #st = :rel, released_at = :now, release_reason = :why",
                       condition="#st = :held", names={"#st": "status"},
                       vals=values(rel=HoldStatus.RELEASED.value, held=HoldStatus.HELD.value, now=now_iso(),
                                   why=cmd.reason or "COMPENSATION"), label="hold")
            self._ledger(txn, hold["fleet_id"], cmd.saga_id, "REFUND", amount)
            txn.outbox(topics.SAGA_REPLIES, env.reply("billing.FundsRefunded", {
                "saga_id": cmd.saga_id, "refunded_cop": amount, "fleet_id": hold["fleet_id"]}, source=SOURCE))
            try:
                execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                log.warning("reembolso ejecutado (compensación)", extra={"amount_cop": amount, "fleet": hold["fleet_id"]})
                return
            except BusinessConditionFailed:
                continue
        raise RuntimeError("no se pudo reembolsar tras varios intentos; se reintentará el mensaje")

    # ---------- Después del pivote: captura ----------
    def capture(self, env: Envelope) -> None:
        cmd = SagaRef.model_validate(env.payload)
        for _ in range(MAX_OCC_RETRIES):
            hold = get(self.client, self.table, **hold_key(cmd.saga_id))
            if hold is None or hold["status"] == HoldStatus.CAPTURED.value:
                return self._reply_only(env, "billing.FundsCaptured", {
                    "saga_id": cmd.saga_id, "captured_cop": hold["amount_cop"] if hold else 0})
            if hold["status"] != HoldStatus.HELD.value:
                log.error("captura sobre retención no vigente", extra={"hold_status": hold["status"]})
                return self._reply_only(env, "billing.FundsCaptured", {"saga_id": cmd.saga_id, "captured_cop": 0})
            wallet = get(self.client, self.table, **wallet_key(hold["fleet_id"]))
            amount = hold["amount_cop"]
            txn = self._wallet_txn(env, wallet, 0, -amount, +amount)
            txn.update(hold_key(cmd.saga_id), "SET #st = :cap, captured_at = :now", condition="#st = :held",
                       names={"#st": "status"},
                       vals=values(cap=HoldStatus.CAPTURED.value, held=HoldStatus.HELD.value, now=now_iso()),
                       label="hold")
            self._ledger(txn, hold["fleet_id"], cmd.saga_id, "CAPTURE", amount)
            txn.outbox(topics.SAGA_REPLIES, env.reply("billing.FundsCaptured", {
                "saga_id": cmd.saga_id, "captured_cop": amount}, source=SOURCE))
            try:
                execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                return
            except BusinessConditionFailed:
                continue
        raise RuntimeError("captura con conflicto persistente; se reintentará")
