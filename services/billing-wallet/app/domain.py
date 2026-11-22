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
    # Contexto para la factura (opcional: los comandos viejos no lo traen).
    station_id: str | None = Field(default=None, max_length=64)
    connector_id: str | None = Field(default=None, max_length=16)
    vehicle_id: str | None = Field(default=None, max_length=32)
    requested_by: str | None = Field(default=None, max_length=128)
    requested_by_email: str | None = Field(default=None, max_length=254)


class RegisterPaymentMethod(BaseModel):
    """Medio de pago = cuenta de cobro de una empresa de flota, con un cupo de crédito.

    El número de cuenta solo se usa para derivar los últimos 4 dígitos y una huella (SHA-256):
    nunca se guarda completo.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    owner: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")
    owner_email: str | None = Field(default=None, max_length=254)
    company_name: str = Field(min_length=2, max_length=80)
    account_number: str = Field(pattern=r"^[0-9]{6,20}$")


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


IVA_RATE = Decimal("0.19")


def payment_method_id(raw_uuid_hex: str) -> str:
    return f"pm-{raw_uuid_hex[:16]}"


def invoice_number(saga_id: str, issued_at: datetime) -> str:
    return f"FAC-{issued_at.astimezone(BOGOTA):%Y%m%d}-{saga_id.replace('-', '')[:8].upper()}"


def split_iva(total_cop: int) -> tuple[int, int]:
    """El total ya incluye IVA del 19 %: devuelve (subtotal, iva) en pesos enteros."""
    subtotal = int((Decimal(total_cop) / (1 + IVA_RATE)).to_integral_value(rounding=ROUND_CEILING))
    return subtotal, total_cop - subtotal


def invoice_key(saga_id: str) -> dict[str, str]:
    return {"pk": f"INVOICE#{saga_id}", "sk": "INVOICE"}
