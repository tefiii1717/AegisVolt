"""Máquina de estados de la SAGA "Reservar y Activar Carga" (orquestación).

    RESERVING ──ConnectorReserved──▶ AUTHORIZING_FUNDS ──FundsHeld──▶ STARTING_CHARGE ──ChargingStarted──▶ CAPTURING ──FundsCaptured──▶ COMPLETED
        │                                   │                               │   (PIVOTE)
        │ReservationFailed                  │FundsHoldRejected              │ChargingFailed
        ▼                                   ▼                               ▼
      FAILED                     COMPENSATING_RESERVATION ◀──FundsRefunded── COMPENSATING_FUNDS
                                            │ConnectorReleased
                                            ▼
                                       COMPENSATED

Las compensaciones van en orden inverso: primero billing (reembolso), luego reservas (liberar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aegis_common import topics


class SagaState(StrEnum):
    RESERVING = "RESERVING"
    AUTHORIZING_FUNDS = "AUTHORIZING_FUNDS"
    STARTING_CHARGE = "STARTING_CHARGE"
    CAPTURING = "CAPTURING"
    COMPLETED = "COMPLETED"
    COMPENSATING_FUNDS = "COMPENSATING_FUNDS"
    COMPENSATING_RESERVATION = "COMPENSATING_RESERVATION"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


TERMINAL = {SagaState.COMPLETED, SagaState.COMPENSATED, SagaState.FAILED}


@dataclass
class Command:
    topic: str
    type: str
    payload: dict[str, Any]


@dataclass
class Decision:
    next_state: SagaState
    commands: list[Command] = field(default_factory=list)
    step: dict[str, Any] | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] | None = None


def reserve_cmd(saga: dict) -> Command:
    return Command(topics.RESERVATION_COMMANDS, "reservation.ReserveConnector", {
        "saga_id": saga["saga_id"], "station_id": saga["station_id"], "connector_id": saga["connector_id"],
        "fleet_id": saga["fleet_id"], "fast_charge": saga.get("fast_charge", False)})


def release_connector_cmd(saga: dict, reason: str) -> Command:
    return Command(topics.RESERVATION_COMMANDS, "reservation.ReleaseConnector",
                   {"saga_id": saga["saga_id"], "reason": reason})


def authorize_cmd(saga: dict) -> Command:
    context = {k: saga[k] for k in ("station_id", "connector_id", "vehicle_id", "requested_by",
                                     "requested_by_email") if saga.get(k)}
    return Command(topics.BILLING_COMMANDS, "billing.AuthorizeHold", {
        "saga_id": saga["saga_id"], "fleet_id": saga["fleet_id"], "estimated_kwh": saga["estimated_kwh"],
        "tier": saga.get("tier", "FAST"), **context})


def release_hold_cmd(saga: dict, reason: str) -> Command:
    return Command(topics.BILLING_COMMANDS, "billing.ReleaseHold", {"saga_id": saga["saga_id"], "reason": reason})


def capture_cmd(saga: dict) -> Command:
    return Command(topics.BILLING_COMMANDS, "billing.CaptureHold", {"saga_id": saga["saga_id"]})


def start_charging_cmd(saga: dict) -> Command:
    return Command(topics.DISPATCH_COMMANDS, "dispatch.StartCharging", {
        "saga_id": saga["saga_id"], "station_id": saga["station_id"], "connector_id": saga["connector_id"],
        "power_kw": saga["power_kw"], "estimated_kwh": saga["estimated_kwh"]})


def cancel_charging_cmd(saga: dict, reason: str) -> Command:
    return Command(topics.DISPATCH_COMMANDS, "dispatch.CancelCharging", {"saga_id": saga["saga_id"], "reason": reason})


def _step(name: str, status: str, detail: dict | None = None) -> dict:
    return {"step": name, "status": status, "detail": detail or {}}


def decide(saga: dict, reply_type: str, payload: dict) -> Decision | None:
    """Función PURA: (estado actual, respuesta) → decisión. None = mensaje fuera de lugar (se ignora)."""
    state = SagaState(saga["state"])
    match (state, reply_type):
        case (SagaState.RESERVING, "reservation.ConnectorReserved"):
            enriched = {**saga, "power_kw": payload["power_kw"], "tier": payload.get("tier", "FAST")}
            return Decision(SagaState.AUTHORIZING_FUNDS, [authorize_cmd(enriched)],
                            _step("RESERVE_CONNECTOR", "SUCCEEDED", payload),
                            {"power_kw": payload["power_kw"], "tier": payload.get("tier", "FAST")})
        case (SagaState.RESERVING, "reservation.ConnectorReservationFailed"):
            return Decision(SagaState.FAILED, [], _step("RESERVE_CONNECTOR", "FAILED", payload),
                            {"failure_reason": payload.get("reason")})
        case (SagaState.AUTHORIZING_FUNDS, "billing.FundsHeld"):
            return Decision(SagaState.STARTING_CHARGE, [start_charging_cmd(saga)],
                            _step("AUTHORIZE_FUNDS", "SUCCEEDED", payload),
                            {"amount_cop": payload.get("amount_cop"), "surge_applied": payload.get("surge_applied"),
                             "multiplier": payload.get("multiplier")})
        case (SagaState.AUTHORIZING_FUNDS, "billing.FundsHoldRejected"):
            return Decision(SagaState.COMPENSATING_RESERVATION,
                            [release_connector_cmd(saga, "FUNDS_REJECTED")],
                            _step("AUTHORIZE_FUNDS", "FAILED", payload), {"failure_reason": payload.get("reason")},
                            {"action": "RELEASE_CONNECTOR", "status": "REQUESTED"})
        case (SagaState.STARTING_CHARGE, "dispatch.ChargingStarted"):
            return Decision(SagaState.CAPTURING, [capture_cmd(saga)], _step("START_CHARGING", "SUCCEEDED", payload))
        case (SagaState.STARTING_CHARGE, "dispatch.ChargingFailed"):
            return Decision(SagaState.COMPENSATING_FUNDS, [release_hold_cmd(saga, payload.get("reason", "CHARGING_FAILED"))],
                            _step("START_CHARGING", "FAILED", payload), {"failure_reason": payload.get("reason")},
                            {"action": "REFUND_FUNDS", "status": "REQUESTED"})
        case (SagaState.CAPTURING, "billing.FundsCaptured"):
            return Decision(SagaState.COMPLETED, [], _step("CAPTURE_FUNDS", "SUCCEEDED", payload))
        case (SagaState.COMPENSATING_FUNDS, "billing.FundsRefunded"):
            return Decision(SagaState.COMPENSATING_RESERVATION,
                            [release_connector_cmd(saga, saga.get("failure_reason") or "COMPENSATION")],
                            _step("REFUND_FUNDS", "COMPENSATED", payload), {},
                            {"action": "REFUND_FUNDS", "status": "DONE", "refunded_cop": payload.get("refunded_cop", 0)})
        case (SagaState.COMPENSATING_RESERVATION, "reservation.ConnectorReleased"):
            return Decision(SagaState.COMPENSATED, [], _step("RELEASE_CONNECTOR", "COMPENSATED", payload), {},
                            {"action": "RELEASE_CONNECTOR", "status": "DONE"})
    return None


def on_timeout(saga: dict) -> Decision:
    """Qué hacer cuando un paso no responde a tiempo. Las compensaciones se REINTENTAN hasta lograrse."""
    state = SagaState(saga["state"])
    if state is SagaState.RESERVING:
        return Decision(SagaState.COMPENSATING_RESERVATION, [release_connector_cmd(saga, "TIMEOUT")],
                        _step("RESERVE_CONNECTOR", "TIMED_OUT"), {"failure_reason": "RESERVATION_TIMEOUT"},
                        {"action": "RELEASE_CONNECTOR", "status": "REQUESTED"})
    if state is SagaState.AUTHORIZING_FUNDS:
        return Decision(SagaState.COMPENSATING_FUNDS, [release_hold_cmd(saga, "TIMEOUT")],
                        _step("AUTHORIZE_FUNDS", "TIMED_OUT"), {"failure_reason": "FUNDS_TIMEOUT"},
                        {"action": "REFUND_FUNDS", "status": "REQUESTED"})
    if state is SagaState.STARTING_CHARGE:
        # Incertidumbre en el pivote: se cancela en el cargador (lápida) y se reembolsa.
        return Decision(SagaState.COMPENSATING_FUNDS,
                        [cancel_charging_cmd(saga, "TIMEOUT"), release_hold_cmd(saga, "CHARGER_TIMEOUT")],
                        _step("START_CHARGING", "TIMED_OUT"), {"failure_reason": "CHARGER_TIMEOUT"},
                        {"action": "REFUND_FUNDS", "status": "REQUESTED"})
    if state is SagaState.CAPTURING:
        return Decision(SagaState.CAPTURING, [capture_cmd(saga)], _step("CAPTURE_FUNDS", "RETRYING"))
    if state is SagaState.COMPENSATING_FUNDS:
        return Decision(SagaState.COMPENSATING_FUNDS,
                        [release_hold_cmd(saga, saga.get("failure_reason") or "RETRY")], _step("REFUND_FUNDS", "RETRYING"))
    if state is SagaState.COMPENSATING_RESERVATION:
        return Decision(SagaState.COMPENSATING_RESERVATION,
                        [release_connector_cmd(saga, saga.get("failure_reason") or "RETRY")],
                        _step("RELEASE_CONNECTOR", "RETRYING"))
    raise ValueError(f"estado terminal sin timeout: {state}")
