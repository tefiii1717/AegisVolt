import asyncio

from fastapi import APIRouter, HTTPException

from aegis_common import topics
from aegis_common.config import get_settings
from aegis_common.runtime import ServiceRuntime

from .projections import Projector

runtime = ServiceRuntime(get_settings())
projector = Projector(runtime.client, runtime.settings.table_name)
router = APIRouter()


@router.get("/stations")
async def stations() -> list[dict]:
    return await asyncio.to_thread(projector.stations)


@router.get("/stations/{station_id}")
async def station(station_id: str) -> dict:
    doc = await asyncio.to_thread(projector.station, station_id)
    if not doc:
        raise HTTPException(404, "station not found")
    return doc


@router.get("/sagas")
async def sagas(limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(projector.sagas, min(max(limit, 1), 100))


@router.get("/sagas/{saga_id}")
async def saga(saga_id: str) -> dict:
    doc = await asyncio.to_thread(projector.saga, saga_id)
    if not doc:
        raise HTTPException(404, "saga not projected yet")
    return doc


@router.get("/owners/{owner}/sagas")
async def owner_sagas(owner: str, limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(projector.owner_sagas, owner, min(max(limit, 1), 100))


@router.get("/owners/{owner}/payment-methods")
async def owner_payment_methods(owner: str) -> list[dict]:
    return await asyncio.to_thread(projector.payment_methods, owner)


@router.get("/owners/{owner}/invoices")
async def owner_invoices(owner: str, limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(projector.invoices, owner, min(max(limit, 1), 200))


@router.get("/invoices")
async def all_invoices(limit: int = 50) -> list[dict]:
    """Solo la consume el BFF para el rol auditor (scope aegisvolt/audit.read)."""
    return await asyncio.to_thread(projector.invoices, None, min(max(limit, 1), 200))


@router.get("/fleets")
async def fleets() -> list[dict]:
    return await asyncio.to_thread(projector.fleets)


app = runtime.create_app(
    title="query-projection-service",
    topics=[topics.STATION_EVENTS, topics.SAGA_EVENTS, topics.BILLING_EVENTS, topics.TELEMETRY_EVENTS,
            topics.DISPATCH_EVENTS],
    handler=projector.handle, routers=[router], publishes=False,
)
