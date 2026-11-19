import asyncio
import logging
import os

from aegis_common import topics
from aegis_common.circuit_breaker import CircuitBreaker
from aegis_common.config import get_settings
from aegis_common.runtime import ServiceRuntime

from .charger import LegacyCsmsGateway
from .handlers import DispatchHandlers

log = logging.getLogger("dispatch")

runtime = ServiceRuntime(get_settings())
breaker = CircuitBreaker("legacy-csms-charger", failure_threshold=int(os.getenv("CB_FAILURE_THRESHOLD", "3")),
                         recovery_timeout_s=float(os.getenv("CB_RECOVERY_TIMEOUT_S", "20")))
runtime.breakers[breaker.name] = breaker
handlers = DispatchHandlers(
    runtime.client, runtime.settings.table_name, runtime.flags,
    LegacyCsmsGateway(os.getenv("LEGACY_CSMS_URL", "http://legacy-csms:8000")), breaker,
    environment=runtime.settings.environment,
)


async def session_watchdog(_: ServiceRuntime) -> None:
    while True:
        try:
            await handlers.watchdog()
        except Exception:  # noqa: BLE001
            log.exception("watchdog de sesiones falló")
        await asyncio.sleep(5)


app = runtime.create_app(title="energy-dispatch-service",
                         topics=[topics.DISPATCH_COMMANDS, topics.TELEMETRY_EVENTS],
                         handler=handlers.handle, background=[session_watchdog])
