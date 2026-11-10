"""Pruebas deterministas de calidad del Data Steward, sin LLM."""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

steward = importlib.import_module("proyecto.agents.data_steward_agent")

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    destino = tmp_path / "banco.db"
    conexion = sqlite3.connect(destino)
    try:
        conexion.executescript(
            (RAIZ / "proyecto/bd/crear_bd.sql").read_text(encoding="utf-8")
        )
    finally:
        conexion.close()

    motor = create_engine(f"sqlite:///{destino}")
    monkeypatch.setattr(steward, "_ENGINE", motor)
    try:
        yield motor
    finally:
        motor.dispose()
        monkeypatch.setattr(steward, "_ENGINE", None)


def test_calidad_usa_clave_declarada_compuesta(engine):
    datos = json.loads(steward.validar_calidad_datos.invoke({"tabla": "raw_saldos"}))

    assert "error" not in datos
    assert datos["origen_clave"] == "calidad_hallazgos_esperados"
    assert datos["clave_usada"] == ["num_cta", "cve_fecha_informacion"]
    assert datos["filas_sobrantes_por_clave"] == 10


def test_calidad_usa_primary_key_declarada(engine):
    datos = json.loads(steward.validar_calidad_datos.invoke({"tabla": "dim_cliente"}))

    assert "error" not in datos
    assert datos["origen_clave"] == "primary_key"
    assert datos["clave_usada"] == ["num_cte"]


@pytest.mark.parametrize(
    "tabla,esperados",
    [
        ("raw_clientes", 5),
        ("raw_saldos", 4),
        ("raw_transacciones", 3),
    ],
)
def test_evaluar_calidad_coincide_con_hallazgos_esperados(engine, tabla, esperados):
    datos = json.loads(steward.evaluar_calidad.invoke({"tabla": tabla}))

    assert "error" not in datos
    assert datos["resumen"] == {
        "esperados": esperados,
        "coinciden": esperados,
        "diferencias": 0,
    }
    assert all(evaluacion["coincide"] for evaluacion in datos["evaluaciones"])


def test_evaluar_calidad_sin_expectativas(engine):
    datos = json.loads(steward.evaluar_calidad.invoke({"tabla": "dim_cliente"}))

    assert "error" not in datos
    assert datos["evaluaciones"] == []
    assert datos["resumen"]["diferencias"] == 0
