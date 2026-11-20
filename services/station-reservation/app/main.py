from aegis_common import topics
from aegis_common.config import get_settings
from aegis_common.runtime import ServiceRuntime

from .handlers import ReservationHandlers

runtime = ServiceRuntime(get_settings())
handlers = ReservationHandlers(runtime.client, runtime.settings.table_name, runtime.flags)

app = runtime.create_app(
    title="station-reservation-service",
    topics=[topics.RESERVATION_COMMANDS, topics.DISPATCH_EVENTS],
    handler=handlers.handle,
)
