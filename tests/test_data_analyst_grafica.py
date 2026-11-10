"""Pruebas de la tool determinista ``graficar_consulta``."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def backend(tmp_path):
    destino = tmp_path / "banco.db"
    conexion = sqlite3.connect(destino)
    try:
        conexion.executescript(
            (RAIZ / "proyecto/bd/crear_bd.sql").read_text(encoding="utf-8")
        )
    finally:
        conexion.close()

    agente = DataAnalystSQLBackend(url_db=f"sqlite:///{destino}")
    try:
        yield agente
    finally:
        if agente._engine is not None:
            agente._engine.dispose()


def _tool(backend, nombre):
    return next(tool for tool in backend._tools if tool.name == nombre)


def _grafica(backend, **argumentos):
    return json.loads(_tool(backend, "graficar_consulta").invoke(argumentos))


def test_superusuario_grafica_agregado_por_segmento(backend):
    backend.configurar_perfil({"rol": "superusuario"})
    resultado = _grafica(
        backend,
        sql=(
            "SELECT segmento, COUNT(*) AS clientes "
            "FROM vw_cliente_publico GROUP BY segmento"
        ),
        tipo="bar",
        eje_x="segmento",
        eje_y="clientes",
    )

    assert "error" not in resultado
    assert base64.b64decode(resultado["png_base64"]).startswith(b"\x89PNG")
    assert resultado["especificacion"]["tipo"] == "bar"
    assert resultado["especificacion"]["eje_x"] == "segmento"
    assert resultado["especificacion"]["eje_y"] == "clientes"


def test_usuario_no_grafica_sin_filtro_de_su_alcance(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})
    resultado = _grafica(
        backend,
        sql=(
            "SELECT segmento, COUNT(*) AS clientes "
            "FROM vw_cliente_publico GROUP BY segmento"
        ),
        tipo="bar",
    )

    assert "error" in resultado
    assert "filtrar" in resultado["error"]


def test_usuario_grafica_su_propio_alcance(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})
    resultado = _grafica(
        backend,
        sql=(
            "SELECT segmento, COUNT(*) AS clientes "
            "FROM vw_cliente_publico WHERE num_cte = 1 GROUP BY segmento"
        ),
        tipo="pie",
        eje_x="segmento",
        eje_y="clientes",
    )

    assert "error" not in resultado
    assert base64.b64decode(resultado["png_base64"]).startswith(b"\x89PNG")


def test_no_grafica_identificadores(backend):
    backend.configurar_perfil({"rol": "superusuario"})
    resultado = _grafica(
        backend,
        sql=(
            "SELECT num_cte, COUNT(*) AS cuentas "
            "FROM dim_cliente GROUP BY num_cte LIMIT 5"
        ),
        tipo="bar",
        eje_x="num_cte",
        eje_y="cuentas",
    )

    assert "error" in resultado
    assert "identificador" in resultado["error"]


def test_rechaza_grafica_de_resultado_truncado(backend):
    backend.configurar_perfil({"rol": "superusuario"})
    resultado = _grafica(
        backend,
        sql="SELECT num_cta, saldo_mes FROM fact_saldos_captacoloca_m",
        tipo="scatter",
        eje_x="num_cta",
        eje_y="saldo_mes",
    )

    assert "error" in resultado
    assert "truncado" in resultado["error"].lower()
