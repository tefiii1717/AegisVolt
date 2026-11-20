from datetime import UTC, datetime, timedelta

from audit import audit

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)
OLD = (NOW - timedelta(minutes=10)).isoformat()
FRESH = (NOW - timedelta(seconds=5)).isoformat()


def test_detecta_huerfanas_y_retenciones_colgadas():
    sagas = [
        {"saga_id": "a", "state": "COMPLETED", "updated_at": OLD},
        {"saga_id": "b", "state": "COMPENSATED", "updated_at": OLD},
        {"saga_id": "c", "state": "STARTING_CHARGE", "updated_at": OLD},     # atascada
        {"saga_id": "d", "state": "RESERVING", "updated_at": FRESH},         # en curso normal
    ]
    holds = [{"saga_id": "b", "status": "HELD", "amount_cop": 46000}, {"saga_id": "a", "status": "CAPTURED"}]
    reservations = [{"saga_id": "b", "status": "ACTIVE", "station_id": "EST", "connector_id": "C1"}]
    report = audit("test", sagas, holds, reservations, {"a": "COMPLETED", "b": "COMPENSATING_RESERVATION"}, now=NOW)
    assert [o["saga_id"] for o in report.orphans] == ["c"]
    assert report.orphan_holds == [{"saga_id": "b", "amount_cop": 46000}]
    assert report.orphan_reservations[0]["connector_id"] == "C1"
    assert report.projection_lag == ["b"]
    assert report.metrics()["SagasCompensated"] == 1 and not report.healthy


def test_sistema_consistente():
    report = audit("prod", [{"saga_id": "a", "state": "COMPENSATED", "updated_at": OLD}],
                   [{"saga_id": "a", "status": "RELEASED"}], [{"saga_id": "a", "status": "RELEASED"}],
                   {"a": "COMPENSATED"}, now=NOW)
    assert report.healthy
