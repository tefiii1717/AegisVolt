"""Bounded context: Energy Dispatch (activación física del cargador y telemetría)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"   # también actúa como lápida si la cancelación llega antes del inicio
    ENDED = "ENDED"


class StartCharging(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    station_id: str
    connector_id: str
    power_kw: float = Field(gt=0, le=400)
    estimated_kwh: float = Field(gt=0, le=1000)


class CancelCharging(BaseModel):
    model_config = ConfigDict(extra="ignore")
    saga_id: str
    reason: str = "SAGA_TIMEOUT"


class MeterValues(BaseModel):
    """Evento de dominio producido por la ACL (no el formato OCPP del legado)."""

    model_config = ConfigDict(extra="ignore")
    station_id: str
    connector_id: str
    legacy_transaction_id: int | None = None
    energy_kwh: float = Field(ge=0)
    power_kw: float = Field(ge=0)
    soc_percent: float | None = Field(default=None, ge=0, le=100)
    measured_at: str


def session_key(saga_id: str) -> dict[str, str]:
    return {"pk": f"SESSION#{saga_id}", "sk": "SESSION"}


def pointer_key(station_id: str, connector_id: str) -> dict[str, str]:
    return {"pk": f"CONNECTOR#{station_id}#{connector_id}", "sk": "ACTIVE_SESSION"}
