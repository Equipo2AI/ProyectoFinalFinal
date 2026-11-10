"""Pruebas de row-level security en las tools auxiliares de Data Analyst."""

from __future__ import annotations

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


def test_usuario_solo_cuenta_y_lista_su_propio_alcance(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})

    descripcion = json.loads(
        _tool(backend, "describir_tabla").invoke({"tabla": "dim_cliente"})
    )
    valores = json.loads(
        _tool(backend, "valores_columna").invoke(
            {
                "tabla": "dim_cliente",
                "columna": "num_cte",
            }
        )
    )

    assert descripcion["filas"] == 1
    assert valores["valores"] == [[1, 1]]


def test_superusuario_conserva_alcance_completo(backend):
    backend.configurar_perfil({"rol": "superusuario"})

    descripcion = json.loads(
        _tool(backend, "describir_tabla").invoke({"tabla": "dim_cliente"})
    )
    valores = json.loads(
        _tool(backend, "valores_columna").invoke(
            {
                "tabla": "dim_cliente",
                "columna": "num_cte",
            }
        )
    )

    assert descripcion["filas"] > 1
    assert len(valores["valores"]) > 1


def test_usuario_no_puede_ejecutar_sin_filtro(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})

    sin_filtro = json.loads(
        _tool(backend, "ejecutar_consulta").invoke(
            {
                "sql": "SELECT COUNT(*) FROM dim_cliente",
            }
        )
    )
    con_filtro = json.loads(
        _tool(backend, "ejecutar_consulta").invoke(
            {
                "sql": "SELECT COUNT(*) FROM dim_cliente WHERE num_cte = 1",
            }
        )
    )

    assert "error" in sin_filtro
    assert con_filtro["filas"] == [[1]]
