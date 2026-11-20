from fastapi import APIRouter, HTTPException

from aegis_common import topics
from aegis_common.config import get_settings
from aegis_common.dynamo import get
from aegis_common.runtime import ServiceRuntime

from .domain import wallet_key
from .handlers import BillingHandlers

runtime = ServiceRuntime(get_settings())
handlers = BillingHandlers(runtime.client, runtime.settings.table_name, runtime.flags)

router = APIRouter(prefix="/internal")


@router.get("/wallets/{fleet_id}")
def wallet(fleet_id: str) -> dict:
    """Lectura directa del agregado (para auditoría de Prefect). La UI lee del modelo CQRS."""
    item = get(runtime.client, runtime.settings.table_name, **wallet_key(fleet_id))
    if not item:
        raise HTTPException(404, "wallet not found")
    return item


app = runtime.create_app(title="billing-wallet-service", topics=[topics.BILLING_COMMANDS],
                         handler=handlers.handle, routers=[router])
