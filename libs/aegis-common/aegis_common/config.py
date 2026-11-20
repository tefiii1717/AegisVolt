from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "test", "prod"]


class ServiceSettings(BaseSettings):
    """Configuración 12-factor. Todo llega por variables de entorno (ConfigMap / Compose)."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", case_sensitive=False)

    service_name: str = Field(default="aegis-service", alias="SERVICE_NAME")
    environment: Environment = Field(default="local", alias="AEGIS_ENV")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    # Endpoint alterno para Floci / DynamoDB Local. Vacío = AWS real (vía Gateway VPCE).
    dynamodb_endpoint: str | None = Field(default=None, alias="DYNAMODB_ENDPOINT")
    table_name: str = Field(default="", alias="TABLE_NAME")
    flags_table: str = Field(default="", alias="FLAGS_TABLE")
    kafka_bootstrap: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP")
    kafka_group_id: str | None = Field(default=None, alias="KAFKA_GROUP_ID")
    # inprocess = el relay corre dentro del proceso; sidecar = lo ejecuta otro contenedor del pod.
    outbox_relay_mode: Literal["inprocess", "sidecar", "off"] = Field(default="inprocess", alias="OUTBOX_RELAY_MODE")
    outbox_poll_interval_s: float = Field(default=0.5, alias="OUTBOX_POLL_INTERVAL_S")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    flags_cache_ttl_s: float = Field(default=3.0, alias="FLAGS_CACHE_TTL_S")

    @property
    def group_id(self) -> str:
        return self.kafka_group_id or f"{self.service_name}-{self.environment}"


@lru_cache
def get_settings() -> ServiceSettings:
    return ServiceSettings()
