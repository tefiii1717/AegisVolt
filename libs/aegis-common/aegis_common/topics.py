"""Contrato de integración: nombres de tópicos Kafka (no modelos de dominio).

Convención: aegisvolt.<bounded-context>.<commands|events>. La clave de partición siempre es el
saga_id (o el id del agregado) para garantizar orden por SAGA.
"""

RESERVATION_COMMANDS = "aegisvolt.reservation.commands"
BILLING_COMMANDS = "aegisvolt.billing.commands"
DISPATCH_COMMANDS = "aegisvolt.dispatch.commands"
SAGA_REPLIES = "aegisvolt.saga.replies"

SAGA_EVENTS = "aegisvolt.saga.events"
STATION_EVENTS = "aegisvolt.station.events"
BILLING_EVENTS = "aegisvolt.billing.events"
DISPATCH_EVENTS = "aegisvolt.dispatch.events"
TELEMETRY_EVENTS = "aegisvolt.telemetry.events"

LEGACY_METER_VALUES = "legacy.ocpp.meter-values"
DEAD_LETTER = "aegisvolt.dead-letter"

ALL_TOPICS = [
    RESERVATION_COMMANDS,
    BILLING_COMMANDS,
    DISPATCH_COMMANDS,
    SAGA_REPLIES,
    SAGA_EVENTS,
    STATION_EVENTS,
    BILLING_EVENTS,
    DISPATCH_EVENTS,
    TELEMETRY_EVENTS,
    LEGACY_METER_VALUES,
    DEAD_LETTER,
]
