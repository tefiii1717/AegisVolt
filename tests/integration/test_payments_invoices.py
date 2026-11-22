"""Medios de pago y facturas: la SAGA cobra contra el cupo de la empresa y la captura emite la factura.

Cada operador solo ve lo suyo en el modelo de lectura (partición OWNER#<usuario>).
"""

import pytest

pytestmark = pytest.mark.asyncio


def register(platform, owner: str, company: str = "Transportes La Sabana S.A.S.", account: str = "0012345678"):
    from svc_billing.domain import RegisterPaymentMethod

    return platform.billing.register_payment_method(RegisterPaymentMethod(
        owner=owner, owner_email=f"{owner}@operador.aegisvolt.co", company_name=company, account_number=account))


async def test_medio_de_pago_no_guarda_la_cuenta_completa(platform):
    pm = register(platform, "u-ana")
    await platform.pump()
    assert pm["payment_method_id"].startswith("pm-") and pm["account_last4"] == "5678"
    wallet = platform.item("billing", f"WALLET#{pm['payment_method_id']}", "WALLET")
    assert wallet["kind"] == "PAYMENT_METHOD" and wallet["available_cop"] == wallet["credit_limit_cop"]
    assert "0012345678" not in str(wallet)  # solo últimos 4 dígitos + huella SHA-256
    listed = platform.projector.payment_methods("u-ana")
    assert [m["company_name"] for m in listed] == ["Transportes La Sabana S.A.S."]
    assert platform.projector.payment_methods("u-otro") == []


async def test_carga_exitosa_emite_una_factura_a_la_empresa(platform):
    pm = register(platform, "u-ana")
    saga = platform.start(fleet_id=pm["payment_method_id"], requested_by="u-ana",
                          requested_by_email="ana@operador.aegisvolt.co", company_name=pm["company_name"],
                          vehicle_id="WKT-512")
    await platform.pump()

    assert platform.engine.load(saga["saga_id"])["state"] == "COMPLETED"
    invoice = platform.item("billing", f"INVOICE#{saga['saga_id']}", "INVOICE")
    assert invoice["company_name"] == "Transportes La Sabana S.A.S." and invoice["account_last4"] == "5678"
    assert invoice["total_cop"] == 40_000 and invoice["subtotal_cop"] + invoice["iva_cop"] == 40_000
    assert invoice["vehicle_id"] == "WKT-512" and invoice["station_id"] == "EST-BOG-080"
    assert invoice["invoice_id"].startswith("FAC-")
    wallet = platform.item("billing", f"WALLET#{pm['payment_method_id']}", "WALLET")
    assert wallet["captured_cop"] == 40_000 and wallet["held_cop"] == 0

    # CQRS: la factura y la carga aparecen SOLO en la partición del operador, y en la vista del auditor.
    assert [i["saga_id"] for i in platform.projector.invoices("u-ana")] == [saga["saga_id"]]
    assert [i["saga_id"] for i in platform.projector.invoices()] == [saga["saga_id"]]
    assert platform.projector.invoices("u-otro") == []
    assert [s["saga_id"] for s in platform.projector.owner_sagas("u-ana")] == [saga["saga_id"]]
    assert platform.projector.owner_sagas("u-otro") == []
    methods = platform.projector.payment_methods("u-ana")
    assert methods[0]["captured_cop"] == 40_000


async def test_carga_compensada_no_factura(platform):
    platform.flags.flags["SIMULATE_CHARGER_HARDWARE_FAULT"] = True
    pm = register(platform, "u-ana")
    saga = platform.start(fleet_id=pm["payment_method_id"], requested_by="u-ana")
    await platform.pump()

    assert platform.engine.load(saga["saga_id"])["state"] == "COMPENSATED"
    assert platform.item("billing", f"INVOICE#{saga['saga_id']}", "INVOICE") is None
    assert platform.projector.invoices("u-ana") == []
    wallet = platform.item("billing", f"WALLET#{pm['payment_method_id']}", "WALLET")
    assert wallet["available_cop"] == wallet["credit_limit_cop"]  # el cupo retenido volvió completo


async def test_captura_duplicada_no_duplica_la_factura(platform):
    pm = register(platform, "u-ana")
    saga = platform.start(fleet_id=pm["payment_method_id"], requested_by="u-ana")
    await platform.pump()
    from aegis_common.envelope import Envelope

    dup = Envelope(type="billing.CaptureHold", correlation_id=saga["saga_id"], source="test", environment="local",
                   payload={"saga_id": saga["saga_id"]})
    await platform.billing.handle(dup)
    await platform.pump()
    assert len(platform.projector.invoices("u-ana")) == 1
    assert platform.item("billing", f"WALLET#{pm['payment_method_id']}", "WALLET")["captured_cop"] == 40_000
