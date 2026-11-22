import asyncio

from fastapi import APIRouter, HTTPException, status

from aegis_common import topics
from aegis_common.config import get_settings
from aegis_common.dynamo import get
from aegis_common.runtime import ServiceRuntime

from .domain import RegisterPaymentMethod, wallet_key
from .handlers import BillingHandlers

runtime = ServiceRuntime(get_settings())
handlers = BillingHandlers(runtime.client, runtime.settings.table_name, runtime.flags,
                           environment=runtime.settings.environment)

router = APIRouter(prefix="/internal")


@router.get("/wallets/{fleet_id}")
def wallet(fleet_id: str) -> dict:
    """Lectura directa del agregado (para auditoría de Prefect). La UI lee del modelo CQRS."""
    item = get(runtime.client, runtime.settings.table_name, **wallet_key(fleet_id))
    if not item:
        raise HTTPException(404, "wallet not found")
    return item


@router.post("/payment-methods", status_code=status.HTTP_201_CREATED)
async def register_payment_method(cmd: RegisterPaymentMethod) -> dict:
    """Comando síncrono del BFF (el operador ya fue autenticado y autorizado allí)."""
    return await asyncio.to_thread(handlers.register_payment_method, cmd)


@router.get("/payment-methods/{pm_id}")
async def payment_method(pm_id: str) -> dict:
    """Fuente de verdad para verificar a quién pertenece un medio de pago antes de reservar."""
    item = await asyncio.to_thread(handlers.payment_method, pm_id)
    if not item:
        raise HTTPException(404, "payment method not found")
    return item


app = runtime.create_app(title="billing-wallet-service", topics=[topics.BILLING_COMMANDS],
                         handler=handlers.handle, routers=[router])
