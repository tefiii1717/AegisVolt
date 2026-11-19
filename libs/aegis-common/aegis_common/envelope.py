"""Sobre (envelope) versionado para todos los mensajes Kafka.

Seguridad (deserialización): los mensajes se leen SOLO como JSON y se validan con Pydantic en
modo estricto, con tamaño máximo. Nunca pickle / yaml.load / eval sobre datos externos.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_MESSAGE_BYTES = 64 * 1024
_TYPE_RE = re.compile(r"^[a-z]+\.[A-Za-z]+$")


class InvalidMessage(ValueError):
    """El mensaje no cumple el contrato; va al dead-letter topic."""


class Envelope(BaseModel):
    # extra="ignore": tolerancia hacia adelante (un canary puede agregar campos nuevos).
    model_config = ConfigDict(extra="ignore", frozen=True)

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    version: int = 1
    correlation_id: str
    causation_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    environment: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if not _TYPE_RE.match(v):
            raise ValueError("type debe tener la forma <contexto>.<NombreMensaje>")
        return v

    @property
    def context(self) -> str:
        return self.type.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.type.split(".", 1)[1]

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    def reply(self, type_: str, payload: dict[str, Any], *, source: str) -> Envelope:
        """Crea un mensaje causado por este (misma correlación)."""
        return Envelope(
            type=type_,
            correlation_id=self.correlation_id,
            causation_id=self.message_id,
            source=source,
            environment=self.environment,
            payload=payload,
        )


def parse_envelope(raw: bytes | str) -> Envelope:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise InvalidMessage(f"mensaje de {len(raw)} bytes excede {MAX_MESSAGE_BYTES}")
    try:
        return Envelope.model_validate_json(raw)
    except ValidationError as exc:
        raise InvalidMessage(str(exc)) from exc
