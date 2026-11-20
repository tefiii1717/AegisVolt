"""Bounded context: Billing & Wallet. Montos en pesos colombianos (COP) enteros, nunca float."""

from __future__ import annotations

from datetime import datetime, time
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

BOGOTA = ZoneInfo("America/Bogota")
PEAK_START, PEAK_END = time(17, 0), time(21, 0)
SURGE_MULTIPLIER = 1.35
ULTRA_PREMIUM = 1.15


class HoldStatus(StrEnum):
    HELD = "HELD"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    RELEASED_EMPTY = "RELEASED_EMPTY"  # lápida anti-mensaje-tardío


class AuthorizeHold(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    fleet_id: str = Field(min_length=1, max_length=64)
    estimated_kwh: float = Field(gt=0, le=1000)
    tier: str = "FAST"


class SagaRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    reason: str | None = None


def is_peak(now: datetime) -> bool:
    local = now.astimezone(BOGOTA).time()
    return PEAK_START <= local < PEAK_END


def quote(estimated_kwh: float, tariff_cop_per_kwh: int, *, tier: str, surge_enabled: bool,
          now: datetime, force_peak: bool = False) -> dict:
    """Cotiza el monto a retener. Puro y determinista → fácil de probar."""
    multiplier = 1.0
    surge_applied = surge_enabled and (force_peak or is_peak(now))
    if surge_applied:
        multiplier *= SURGE_MULTIPLIER
    if tier == "ULTRA":
        multiplier *= ULTRA_PREMIUM
    # Decimal: evita errores de punto flotante (40 * 1150 * 1.35 debe ser 62100 exactos, no 62101).
    exact = Decimal(str(estimated_kwh)) * Decimal(tariff_cop_per_kwh) * Decimal(str(round(multiplier, 4)))
    amount = int(exact.to_integral_value(rounding=ROUND_CEILING))
    return {"amount_cop": amount, "tariff_cop_per_kwh": tariff_cop_per_kwh, "multiplier": round(multiplier, 4),
            "surge_applied": surge_applied}


def wallet_key(fleet_id: str) -> dict[str, str]:
    return {"pk": f"WALLET#{fleet_id}", "sk": "WALLET"}


def hold_key(saga_id: str) -> dict[str, str]:
    return {"pk": f"HOLD#{saga_id}", "sk": "HOLD"}
