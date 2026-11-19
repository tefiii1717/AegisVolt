"""Fábrica de aplicaciones FastAPI para los microservicios: health checks, middleware de
correlación, ciclo de vida del consumidor Kafka y del relay del outbox."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .bus import Handler, KafkaBus, KafkaSubscriber, MessageBus
from .circuit_breaker import CircuitBreaker
from .config import ServiceSettings
from .dynamo import make_client
from .flags import FeatureFlags
from .logs import configure_logging, correlation_id_var
from .outbox import OutboxRelay

log = logging.getLogger("aegis.runtime")

BackgroundTask = Callable[["ServiceRuntime"], Coroutine[Any, Any, None]]


class ServiceRuntime:
    def __init__(self, settings: ServiceSettings, *, client=None, bus: MessageBus | None = None, flags=None) -> None:
        self.settings = settings
        configure_logging(settings.service_name, settings.environment, settings.log_level)
        self.client = client or make_client(settings.aws_region, settings.dynamodb_endpoint)
        self.bus: MessageBus = bus or KafkaBus(settings.kafka_bootstrap, settings.service_name)
        self.flags = flags or (FeatureFlags(self.client, settings.flags_table, ttl_s=settings.flags_cache_ttl_s)
                               if settings.flags_table else None)
        self.breakers: dict[str, CircuitBreaker] = {}
        self.relay: OutboxRelay | None = None
        self.subscriber: KafkaSubscriber | None = None
        self.started_at = time.time()
        self._tasks: list[asyncio.Task] = []

    def create_app(self, *, title: str, topics: list[str] | None = None, handler: Handler | None = None,
                   routers: list[APIRouter] | None = None, background: list[BackgroundTask] | None = None,
                   publishes: bool = True) -> FastAPI:
        s = self.settings

        @asynccontextmanager
        async def lifespan(_: FastAPI):
            if publishes or topics:
                for attempt in range(1, 31):
                    try:
                        await self.bus.start()
                        break
                    except Exception:  # noqa: BLE001
                        log.warning("Kafka no disponible al iniciar", extra={"attempt": attempt})
                        await asyncio.sleep(2)
            if publishes and s.outbox_relay_mode == "inprocess" and s.table_name:
                self.relay = OutboxRelay(self.client, s.table_name, self.bus)
                self._tasks.append(asyncio.create_task(self.relay.run_forever(s.outbox_poll_interval_s)))
            if topics and handler:
                self.subscriber = KafkaSubscriber(s.kafka_bootstrap, s.group_id, topics, handler, self.bus)
                self._tasks.append(asyncio.create_task(self.subscriber.run()))
            for task in background or []:
                self._tasks.append(asyncio.create_task(task(self)))
            log.info("servicio iniciado", extra={"topics": topics or [], "relay": s.outbox_relay_mode})
            yield
            for t in self._tasks:
                t.cancel()
            await self.bus.stop()

        app = FastAPI(title=title, version=os.getenv("APP_VERSION", "dev"), lifespan=lifespan,
                      docs_url="/docs" if s.environment != "prod" else None, redoc_url=None)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
            token = correlation_id_var.set(cid)
            started = time.perf_counter()
            try:
                response: Response = await call_next(request)
            finally:
                correlation_id_var.reset(token)
            response.headers["x-correlation-id"] = cid
            if not request.url.path.startswith("/health"):
                log.info("http", extra={"method": request.method, "path": request.url.path,
                                        "status": response.status_code, "correlation_id": cid,
                                        "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            return response

        @app.get("/health/live", include_in_schema=False)
        async def live() -> dict:
            return {"status": "UP"}

        @app.get("/health/ready", include_in_schema=False)
        async def ready() -> JSONResponse:
            checks: dict[str, str] = {}
            if s.table_name:
                try:
                    await asyncio.to_thread(self.client.describe_table, TableName=s.table_name)
                    checks["dynamodb"] = "UP"
                except Exception:  # noqa: BLE001
                    checks["dynamodb"] = "DOWN"
            if self.subscriber is not None:
                checks["kafka_consumer"] = "UP" if self.subscriber.healthy else "DOWN"
            ok = all(v == "UP" for v in checks.values())
            return JSONResponse({"status": "UP" if ok else "DOWN", "checks": checks}, status_code=200 if ok else 503)

        @app.get("/health", include_in_schema=False)
        async def details() -> dict:
            return {
                "service": s.service_name,
                "environment": s.environment,
                "version": app.version,
                "uptime_s": round(time.time() - self.started_at),
                "circuit_breakers": [b.snapshot() for b in self.breakers.values()],
                "outbox": {"published_total": self.relay.published_total if self.relay else None,
                           "mode": s.outbox_relay_mode},
                "flags": self.flags.snapshot() if self.flags else {},
            }

        for router in routers or []:
            app.include_router(router)
        return app
