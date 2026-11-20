"""Reglas de auditoría de consistencia de la SAGA (funciones puras, probadas sin Prefect)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

TERMINAL = {"COMPLETED", "COMPENSATED", "FAILED"}


@dataclass
class AuditReport:
    environment: str
    total: int = 0
    completed: int = 0
    compensated: int = 0
    failed: int = 0
    in_flight: int = 0
    orphans: list[dict] = field(default_factory=list)            # SAGAs atascadas sin terminar
    orphan_holds: list[dict] = field(default_factory=list)       # saldo retenido de SAGAs ya terminadas
    orphan_reservations: list[dict] = field(default_factory=list)
    projection_lag: list[str] = field(default_factory=list)      # modelo de lectura desalineado (CQRS)

    @property
    def healthy(self) -> bool:
        return not (self.orphans or self.orphan_holds or self.orphan_reservations)

    def metrics(self) -> dict[str, int]:
        return {"SagasTotal": self.total, "SagasCompleted": self.completed, "SagasCompensated": self.compensated,
                "SagasFailed": self.failed, "SagasInFlight": self.in_flight, "SagasOrphaned": len(self.orphans),
                "OrphanHolds": len(self.orphan_holds), "OrphanReservations": len(self.orphan_reservations),
                "ProjectionLag": len(self.projection_lag)}


def _age_s(iso: str | None, now: datetime) -> float:
    if not iso:
        return 0.0
    return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()


def audit(environment: str, sagas: list[dict], holds: list[dict], reservations: list[dict],
          projected: dict[str, str], *, now: datetime | None = None, stale_after_s: int = 120) -> AuditReport:
    now = now or datetime.now(UTC)
    r = AuditReport(environment=environment, total=len(sagas))
    by_id = {s["saga_id"]: s for s in sagas}
    for s in sagas:
        state = s["state"]
        r.completed += state == "COMPLETED"
        r.compensated += state == "COMPENSATED"
        r.failed += state == "FAILED"
        if state not in TERMINAL:
            r.in_flight += 1
            if _age_s(s.get("updated_at"), now) > stale_after_s or s.get("stuck"):
                r.orphans.append({"saga_id": s["saga_id"], "state": state, "updated_at": s.get("updated_at")})
        if projected.get(s["saga_id"]) not in (None, state) and _age_s(s.get("updated_at"), now) > 10:
            r.projection_lag.append(s["saga_id"])
    for h in holds:
        saga = by_id.get(h["saga_id"])
        if h.get("status") == "HELD" and saga and saga["state"] in {"COMPENSATED", "FAILED"}:
            r.orphan_holds.append({"saga_id": h["saga_id"], "amount_cop": h.get("amount_cop")})
    for res in reservations:
        saga = by_id.get(res["saga_id"])
        if res.get("status") == "ACTIVE" and saga and saga["state"] in {"COMPENSATED", "FAILED"}:
            r.orphan_reservations.append({"saga_id": res["saga_id"], "station_id": res.get("station_id"),
                                          "connector_id": res.get("connector_id")})
    return r


def cutoff(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)
