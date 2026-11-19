import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, status

from aegis_common import topics
from aegis_common.config import get_settings
from aegis_common.runtime import ServiceRuntime

from .engine import SagaEngine, StartSagaRequest

log = logging.getLogger("orchestrator")

runtime = ServiceRuntime(get_settings())
engine = SagaEngine(runtime.client, runtime.settings.table_name, runtime.settings.environment)
router = APIRouter()


@router.post("/sagas/reserve-and-charge", status_code=status.HTTP_202_ACCEPTED)
async def start_saga(req: StartSagaRequest, idempotency_key: str | None = Header(default=None)) -> dict:
    """Disparador de la transacción distribuida. Responde 202: el resultado llega de forma asíncrona."""
    return await asyncio.to_thread(engine.start, req, idempotency_key)


@router.get("/sagas/{saga_id}")
async def get_saga(saga_id: str) -> dict:
    saga = await asyncio.to_thread(engine.load, saga_id)
    if not saga:
        raise HTTPException(404, "saga not found")
    return saga


@router.get("/internal/sagas")
async def list_sagas(limit: int = 50) -> list[dict]:
    """Lectura del log de SAGA (fuente de verdad) para la auditoría de Prefect."""
    return await asyncio.to_thread(engine.list_recent, min(limit, 200))


async def timeout_sweeper(_: ServiceRuntime) -> None:
    while True:
        try:
            await asyncio.to_thread(engine.sweep_timeouts)
        except Exception:  # noqa: BLE001
            log.exception("sweeper de timeouts falló")
        await asyncio.sleep(3)


app = runtime.create_app(title="saga-orchestrator", topics=[topics.SAGA_REPLIES], handler=engine.handle,
                         routers=[router], background=[timeout_sweeper])
