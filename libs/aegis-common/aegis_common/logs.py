"""Logs estructurados JSON con correlation_id (= saga_id) para CloudWatch.

Cada línea es un objeto JSON independiente: Fluent Bit (sidecar) la envía tal cual al grupo
/aegisvolt/<env>/<service> y CloudWatch Logs Insights puede filtrar por `correlation_id`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment
        self.version = os.getenv("APP_VERSION", "dev")  # dimensión para comparar canary vs estable

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "version": self.version,
            "correlation_id": getattr(record, "correlation_id", None) or correlation_id_var.get(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in doc and not key.startswith("_"):
                doc[key] = value
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, ensure_ascii=False)


class ErrorRateCounter(logging.Handler):
    """Cuenta logs ERROR en una ventana deslizante de 60 s. Lo consulta el análisis del canary
    (Argo Rollouts, proveedor web) en /health/stats de los pods de la versión nueva."""

    def __init__(self, window_s: float = 60.0) -> None:
        super().__init__(level=logging.ERROR)
        self.window_s = window_s
        self._events: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._events.append(record.created)

    def per_window(self) -> int:
        import time

        cutoff = time.time() - self.window_s
        self._events = [t for t in self._events if t >= cutoff]
        return len(self._events)


error_rate = ErrorRateCounter()


def configure_logging(service: str, environment: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service, environment))
    root = logging.getLogger()
    root.handlers[:] = [handler, error_rate]
    # Patrón Sidecar: si LOG_FILE está definido, las mismas líneas JSON se escriben en un volumen
    # compartido (emptyDir) que el contenedor Fluent Bit del pod envía a CloudWatch.
    log_file = os.getenv("LOG_FILE")
    if log_file:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(log_file, maxBytes=20 * 1024 * 1024, backupCount=2)
        file_handler.setFormatter(JsonFormatter(service, environment))
        root.addHandler(file_handler)
    root.setLevel(level.upper())
    for noisy in ("aiokafka", "botocore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class correlation_scope:  # noqa: N801 - se usa como `with correlation_scope(id):`
    """Fija el correlation_id durante el procesamiento de un mensaje o request."""

    def __init__(self, correlation_id: str | None) -> None:
        self._cid = correlation_id
        self._token: contextvars.Token | None = None

    def __enter__(self) -> None:
        self._token = correlation_id_var.set(self._cid)

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            correlation_id_var.reset(self._token)
