"""Paso 2 de la SAGA (retención de fondos) y sus compensaciones.

Modelo Hold → Capture:
  AuthorizeHold : available -= monto, held += monto   (Paso 2, compensable)
  ReleaseHold   : available += monto, held -= monto   (compensación = "reembolso")
  CaptureHold   : held -= monto, captured += monto    (después del pivote, solo reintentable)
                  + emite la FACTURA a la empresa del medio de pago (misma transacción → exactamente una).

Medios de pago: cada operador registra empresas a las que se facturan sus cargas. Cada medio de pago
es una cuenta de cobro (WALLET#pm-…) con un cupo de crédito; la SAGA retiene y captura contra ese cupo.

Concurrencia: control optimista con `version` en la billetera (varias SAGAs de la misma flota).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime

from aegis_common import topics
from aegis_common.dynamo import BusinessConditionFailed, Txn, execute, get, now_iso, values
from aegis_common.envelope import Envelope

from .domain import (
    AuthorizeHold,
    HoldStatus,
    RegisterPaymentMethod,
    SagaRef,
    hold_key,
    invoice_key,
    invoice_number,
    payment_method_id,
    quote,
    split_iva,
    wallet_key,
)

log = logging.getLogger("billing")
SOURCE = "billing-wallet-service"
CONSUMER = "billing"
MAX_OCC_RETRIES = 5


class BillingHandlers:
    def __init__(self, client, table: str, flags, *, tariff_cop_per_kwh: int | None = None,
                 force_peak: bool | None = None, clock=lambda: datetime.now(UTC),
                 environment: str | None = None, credit_limit_cop: int | None = None) -> None:
        self.client, self.table, self.flags, self.clock = client, table, flags, clock
        self.environment = environment or os.getenv("AEGIS_ENV", "local")
        self.credit_limit = credit_limit_cop or int(os.getenv("PAYMENT_METHOD_CREDIT_LIMIT_COP", "3000000"))
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
            context = cmd.model_dump(include={"station_id", "connector_id", "vehicle_id", "requested_by",
                                              "requested_by_email"}, exclude_none=True)
            context["energy_kwh"] = cmd.estimated_kwh
            txn.put({**hold_key(cmd.saga_id), "status": HoldStatus.HELD.value, "fleet_id": cmd.fleet_id,
                     "amount_cop": amount, "quote": q, "context": context, "created_at": now_iso()},
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
            invoice = self._invoice(cmd.saga_id, hold, wallet)
            txn.put({**invoice_key(cmd.saga_id), **invoice}, condition="attribute_not_exists(pk)", label="invoice")
            txn.outbox(topics.BILLING_EVENTS, env.reply("billing.InvoiceIssued", invoice, source=SOURCE),
                       key=hold["fleet_id"])
            txn.outbox(topics.SAGA_REPLIES, env.reply("billing.FundsCaptured", {
                "saga_id": cmd.saga_id, "captured_cop": amount, "invoice_id": invoice["invoice_id"]}, source=SOURCE))
            try:
                execute(self.client, txn, message_id=env.message_id, consumer=CONSUMER)
                log.info("factura emitida", extra={"invoice": invoice["invoice_id"], "total_cop": amount})
                return
            except BusinessConditionFailed:
                continue
        raise RuntimeError("captura con conflicto persistente; se reintentará")

    # ---------- Factura (se emite en la misma transacción que la captura) ----------
    def _invoice(self, saga_id: str, hold: dict, wallet: dict) -> dict:
        issued = self.clock()
        total = hold["amount_cop"]
        subtotal, iva = split_iva(total)
        ctx, q = hold.get("context", {}), hold.get("quote", {})
        invoice = {
            "invoice_id": invoice_number(saga_id, issued), "saga_id": saga_id, "payment_method_id": hold["fleet_id"],
            "company_name": wallet.get("company_name") or wallet.get("name") or hold["fleet_id"],
            "account_last4": wallet.get("account_last4"), "owner": wallet.get("owner") or ctx.get("requested_by"),
            "requested_by_email": ctx.get("requested_by_email"), "station_id": ctx.get("station_id"),
            "connector_id": ctx.get("connector_id"), "vehicle_id": ctx.get("vehicle_id"),
            "energy_kwh": ctx.get("energy_kwh", 0), "tariff_cop_per_kwh": q.get("tariff_cop_per_kwh"),
            "multiplier": q.get("multiplier"), "surge_applied": q.get("surge_applied"),
            "subtotal_cop": subtotal, "iva_cop": iva, "total_cop": total, "currency": "COP",
            "issued_at": issued.isoformat(),
        }
        return {k: v for k, v in invoice.items() if v is not None}

    # ---------- Medios de pago (comando síncrono desde el BFF) ----------
    def register_payment_method(self, cmd: RegisterPaymentMethod) -> dict:
        pm_id = payment_method_id(uuid.uuid4().hex)
        now = now_iso()
        method = {
            "payment_method_id": pm_id, "owner": cmd.owner, "owner_email": cmd.owner_email,
            "company_name": cmd.company_name, "account_last4": cmd.account_number[-4:],
            "credit_limit_cop": self.credit_limit, "created_at": now,
        }
        wallet = {
            **wallet_key(pm_id), "fleet_id": pm_id, "kind": "PAYMENT_METHOD", **method,
            "name": cmd.company_name, "account_fingerprint": hashlib.sha256(cmd.account_number.encode()).hexdigest(),
            "available_cop": self.credit_limit, "held_cop": 0, "captured_cop": 0, "version": 1, "updated_at": now,
        }
        registered = Envelope(type="billing.PaymentMethodRegistered", correlation_id=pm_id, source=SOURCE,
                              environment=self.environment, payload={k: v for k, v in method.items() if v is not None})
        balance = Envelope(type="billing.WalletBalanceChanged", correlation_id=pm_id, source=SOURCE,
                           environment=self.environment,
                           payload={"fleet_id": pm_id, "available_cop": self.credit_limit, "held_cop": 0,
                                    "captured_cop": 0, "version": 1, "changed_at": now})
        txn = (Txn(self.table)
               .put(wallet, condition="attribute_not_exists(pk)", label="wallet")
               .outbox(topics.BILLING_EVENTS, registered, key=pm_id)
               .outbox(topics.BILLING_EVENTS, balance, key=pm_id))
        execute(self.client, txn)
        log.info("medio de pago registrado", extra={"payment_method": pm_id, "owner": cmd.owner})
        return {**method, "available_cop": self.credit_limit, "held_cop": 0, "captured_cop": 0}

    def payment_method(self, pm_id: str) -> dict | None:
        wallet = get(self.client, self.table, **wallet_key(pm_id))
        if not wallet or wallet.get("kind") != "PAYMENT_METHOD":
            return None
        keep = ("payment_method_id", "owner", "owner_email", "company_name", "account_last4", "credit_limit_cop",
                "available_cop", "held_cop", "captured_cop", "created_at")
        return {k: wallet[k] for k in keep if k in wallet}
