import pytest

from app.saga import SagaState, decide, on_timeout

BASE = {"saga_id": "s1", "fleet_id": "f", "station_id": "EST", "connector_id": "C1", "estimated_kwh": 10,
        "power_kw": 60}


def saga(state: SagaState, **extra) -> dict:
    return {**BASE, "state": state.value, **extra}


def test_reserva_exitosa_pide_retencion_con_potencia_del_conector():
    d = decide(saga(SagaState.RESERVING), "reservation.ConnectorReserved", {"power_kw": 150, "tier": "ULTRA"})
    assert d.next_state is SagaState.AUTHORIZING_FUNDS
    assert d.commands[0].type == "billing.AuthorizeHold" and d.commands[0].payload["tier"] == "ULTRA"


def test_fondos_rechazados_compensa_solo_reserva():
    d = decide(saga(SagaState.AUTHORIZING_FUNDS), "billing.FundsHoldRejected", {"reason": "INSUFFICIENT_FUNDS"})
    assert d.next_state is SagaState.COMPENSATING_RESERVATION
    assert [c.type for c in d.commands] == ["reservation.ReleaseConnector"]


def test_fallo_en_paso3_compensa_billing_primero():
    d = decide(saga(SagaState.STARTING_CHARGE), "dispatch.ChargingFailed", {"reason": "HARDWARE_FAULT_SIMULATED"})
    assert d.next_state is SagaState.COMPENSATING_FUNDS
    assert [c.type for c in d.commands] == ["billing.ReleaseHold"]
    d2 = decide(saga(SagaState.COMPENSATING_FUNDS, failure_reason="HW"), "billing.FundsRefunded", {"refunded_cop": 1})
    assert d2.next_state is SagaState.COMPENSATING_RESERVATION
    assert d2.commands[0].type == "reservation.ReleaseConnector"


@pytest.mark.parametrize("state,reply", [
    (SagaState.RESERVING, "billing.FundsHeld"),            # fuera de secuencia
    (SagaState.CAPTURING, "dispatch.ChargingFailed"),      # después del pivote no se compensa
    (SagaState.COMPENSATING_FUNDS, "dispatch.ChargingStarted"),
])
def test_respuestas_fuera_de_secuencia_se_ignoran(state, reply):
    assert decide(saga(state), reply, {}) is None


def test_timeout_en_pivote_cancela_cargador_y_reembolsa():
    d = on_timeout(saga(SagaState.STARTING_CHARGE))
    assert [c.type for c in d.commands] == ["dispatch.CancelCharging", "billing.ReleaseHold"]


def test_timeout_en_captura_solo_reintenta():
    d = on_timeout(saga(SagaState.CAPTURING))
    assert d.next_state is SagaState.CAPTURING and d.commands[0].type == "billing.CaptureHold"
