from datetime import UTC, datetime

from app.domain import is_peak, quote

PEAK = datetime(2026, 11, 19, 23, 30, tzinfo=UTC)      # 18:30 en Bogotá
OFF_PEAK = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)   # 10:00 en Bogotá


def test_hora_pico_en_zona_horaria_de_bogota():
    assert is_peak(PEAK) and not is_peak(OFF_PEAK)


def test_sin_surge_cobra_tarifa_base():
    q = quote(40, 1150, tier="FAST", surge_enabled=True, now=OFF_PEAK)
    assert q == {"amount_cop": 46_000, "tariff_cop_per_kwh": 1150, "multiplier": 1.0, "surge_applied": False}


def test_surge_solo_si_la_flag_esta_activa_y_es_hora_pico():
    assert quote(40, 1150, tier="FAST", surge_enabled=False, now=PEAK)["surge_applied"] is False
    q = quote(40, 1150, tier="FAST", surge_enabled=True, now=PEAK)
    assert q["surge_applied"] and q["amount_cop"] == 62_100


def test_ultra_tiene_prima_y_montos_enteros():
    q = quote(33.3, 1150, tier="ULTRA", surge_enabled=False, now=OFF_PEAK)
    assert isinstance(q["amount_cop"], int) and q["multiplier"] == 1.15


def test_iva_incluido_suma_exacta():
    from datetime import UTC, datetime

    from app.domain import invoice_number, split_iva

    for total in (40_000, 62_100, 1, 999_999):
        subtotal, iva = split_iva(total)
        assert subtotal + iva == total and iva >= 0
    assert split_iva(119_000) == (100_000, 19_000)
    number = invoice_number("3f2a9c1e-5b7d-4e8a", datetime(2026, 11, 22, 3, 0, tzinfo=UTC))
    assert number == "FAC-20261121-3F2A9C1E"  # fecha en hora de Bogotá
