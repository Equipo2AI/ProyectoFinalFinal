"""Pruebas de la resolución determinista de periodos."""

from __future__ import annotations

import pytest

from proyecto.periods import resolver_periodo


def test_anio_mes_y_dia():
    assert resolver_periodo("2026").inicio == "2026-01-01"
    assert resolver_periodo("2026").fin == "2026-12-31"
    assert resolver_periodo("2026-02").inicio == "2026-02-01"
    assert resolver_periodo("2026-02").fin == "2026-02-28"
    assert resolver_periodo("2026-02-15").clave_inicio == 20260215
    assert resolver_periodo("2026-02-15").tipo == "dia"


def test_mes_por_nombre_y_trimestre():
    febrero = resolver_periodo("febrero 2026")
    trimestre = resolver_periodo("1T 2026")
    q2 = resolver_periodo("2026-Q2")

    assert (febrero.inicio, febrero.fin) == ("2026-02-01", "2026-02-28")
    assert (trimestre.inicio, trimestre.fin) == ("2026-01-01", "2026-03-31")
    assert (q2.inicio, q2.fin) == ("2026-04-01", "2026-06-30")
    assert q2.anio_mes_inicio == 202604
    assert q2.anio_mes_fin == 202606


def test_periodos_relativos_con_hoy_fijo():
    assert resolver_periodo("mes pasado", hoy="2026-11-10").inicio == "2026-10-01"
    assert resolver_periodo("mes pasado", hoy="2026-11-10").fin == "2026-10-31"
    assert resolver_periodo("este año", hoy="2026-11-10").fin == "2026-12-31"
    assert resolver_periodo("últimos 30 días", hoy="2026-11-10").inicio == "2026-10-12"
    assert resolver_periodo("último trimestre", hoy="2026-11-10").inicio == "2026-07-01"


def test_expresion_no_soportada_falla():
    with pytest.raises(ValueError, match="No pude interpretar"):
        resolver_periodo("cuando sea conveniente", hoy="2026-11-10")

    with pytest.raises(ValueError, match="entre 01 y 12"):
        resolver_periodo("2026-13")
