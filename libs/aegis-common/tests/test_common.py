import json

import pytest

from aegis_common.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from aegis_common.envelope import MAX_MESSAGE_BYTES, Envelope, InvalidMessage, parse_envelope
from aegis_common.flags import FeatureFlags


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_envelope_roundtrip_y_reply_conserva_correlacion():
    env = Envelope(type="billing.AuthorizeHold", correlation_id="saga-1", source="t", environment="dev",
                   payload={"x": 1})
    parsed = parse_envelope(env.to_bytes())
    reply = parsed.reply("billing.FundsHeld", {}, source="billing")
    assert reply.correlation_id == "saga-1" and reply.causation_id == env.message_id


@pytest.mark.parametrize("raw", [
    b"not json",
    b'{"type": "sin-punto", "correlation_id": "x", "source": "s", "environment": "dev"}',
    # Carga con apariencia de objeto serializado (pickle/yaml): se rechaza por ser JSON inválido
    b"\x80\x04\x95cos\nsystem\n",
])
def test_envelope_rechaza_mensajes_invalidos(raw):
    with pytest.raises(InvalidMessage):
        parse_envelope(raw)


def test_envelope_rechaza_mensajes_gigantes():
    body = json.dumps({"type": "a.B", "correlation_id": "x", "source": "s", "environment": "dev",
                       "payload": {"blob": "x" * MAX_MESSAGE_BYTES}}).encode()
    with pytest.raises(InvalidMessage):
        parse_envelope(body)


def test_envelope_ignora_campos_nuevos_de_un_canary():
    raw = b'{"type":"a.B","correlation_id":"x","source":"s","environment":"dev","campo_v2":true}'
    assert parse_envelope(raw).type == "a.B"


async def test_circuit_breaker_ciclo_completo():
    clock = Clock()
    cb = CircuitBreaker("hw", failure_threshold=2, recovery_timeout_s=10, clock=clock)

    async def boom():
        raise ConnectionError("x")

    async def ok():
        return "ok"

    for _ in range(2):
        with pytest.raises(ConnectionError):
            await cb.call(boom)
    assert cb.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(ok)  # fail-fast, ni siquiera se llama
    clock.t = 11
    assert cb.state is CircuitState.HALF_OPEN
    with pytest.raises(ConnectionError):
        await cb.call(boom)  # la prueba falla → vuelve a OPEN
    assert cb.state is CircuitState.OPEN
    clock.t = 22
    assert await cb.call(ok) == "ok"
    assert cb.state is CircuitState.CLOSED
    assert cb.snapshot()["rejected"] == 1


class FakeDdb:
    def __init__(self) -> None:
        self.enabled = False
        self.scans = 0

    def scan(self, **_):
        self.scans += 1
        return {"Items": [{"pk": {"S": "FLAG#SIMULATE_CHARGER_HARDWARE_FAULT"}, "sk": {"S": "FLAG"},
                           "enabled": {"BOOL": self.enabled}}]}


def test_flags_cambian_sin_redesplegar_tras_ttl():
    ddb, clock = FakeDdb(), Clock()
    flags = FeatureFlags(ddb, "aegisvolt-feature-flags-dev", ttl_s=3, clock=clock)
    assert flags.is_enabled("SIMULATE_CHARGER_HARDWARE_FAULT") is False
    ddb.enabled = True  # la Lambda flags-admin cambió la tabla
    clock.t = 1
    assert flags.is_enabled("SIMULATE_CHARGER_HARDWARE_FAULT") is False  # aún en caché
    clock.t = 4
    assert flags.is_enabled("SIMULATE_CHARGER_HARDWARE_FAULT") is True
    assert ddb.scans == 2
