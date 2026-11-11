"""Pruebas del catálogo de métricas y de las tools de resolución."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend
from proyecto.metrics import obtener_metrica

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def backend(tmp_path, sesion_local):
    destino = tmp_path / "banco.db"
    conexion = sqlite3.connect(destino)
    try:
        conexion.executescript(
            (RAIZ / "proyecto/bd/crear_bd.sql").read_text(encoding="utf-8")
        )
    finally:
        conexion.close()

    sesion_local(destino)
    agente = DataAnalystSQLBackend(url_db=f"sqlite:///{destino}")
    agente._autenticar_prueba = lambda rol: sesion_local(destino, rol)
    try:
        yield agente
    finally:
        if agente._engine is not None:
            agente._engine.dispose()


def _tool(backend, nombre):
    return next(tool for tool in backend._tools if tool.name == nombre)


def _llamar(backend, nombre, argumentos=None):
    return json.loads(_tool(backend, nombre).invoke(argumentos or {}))


def test_catalogo_expone_metricas_y_dimensiones(backend):
    datos = _llamar(backend, "catalogo_metricas")
    nombres = {metrica["nombre"] for metrica in datos["metricas"]}

    assert {"clientes", "saldo_total", "transacciones"} <= nombres
    assert obtener_metrica("clientes").unidad == "clientes"
    assert "segmento" in obtener_metrica("clientes").dimensiones


def test_consultar_metrica_sin_agrupacion(backend):
    backend._autenticar_prueba("auditor")
    backend.configurar_perfil({"rol": "superusuario"})
    datos = _llamar(backend, "consultar_metrica", {"metrica": "clientes"})

    assert "error" not in datos
    assert datos["metrica"] == "clientes"
    assert datos["unidad"] == "clientes"
    assert datos["filas"][0][0] > 0
    assert "SELECT COUNT(DISTINCT" in datos["sql"]


def test_consultar_metrica_agrupada_y_ordenada(backend):
    backend._autenticar_prueba("auditor")
    backend.configurar_perfil({"rol": "superusuario"})
    datos = _llamar(
        backend,
        "consultar_metrica",
        {"metrica": "clientes", "agrupar_por": "segmento"},
    )

    assert datos["columnas"] == ["segmento", "clientes"]
    valores = [fila[1] for fila in datos["filas"]]
    assert valores == sorted(valores, reverse=True)
    assert sum(valores) > 0


def test_consultar_metrica_respeta_el_alcance_del_usuario(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})
    datos = _llamar(
        backend,
        "consultar_metrica",
        {"metrica": "clientes", "agrupar_por": "segmento"},
    )

    assert "error" not in datos
    assert sum(fila[1] for fila in datos["filas"]) == 1
    assert "num_cte = 1" in datos["sql"]


def test_consultar_metrica_rechaza_metrica_y_dimension_desconocidas(backend):
    backend._autenticar_prueba("auditor")
    backend.configurar_perfil({"rol": "superusuario"})
    inexistente = _llamar(backend, "consultar_metrica", {"metrica": "utilidad"})
    dimension = _llamar(
        backend,
        "consultar_metrica",
        {"metrica": "clientes", "agrupar_por": "num_cte"},
    )

    assert "error" in inexistente
    assert "catálogo" in inexistente["error"]
    assert "error" in dimension
    assert "Dimensiones" in dimension["error"]


def test_resolver_valor_encuentra_el_valor_canonico(backend):
    backend._autenticar_prueba("auditor")
    backend.configurar_perfil({"rol": "superusuario"})
    datos = _llamar(
        backend,
        "resolver_valor",
        {
            "tabla": "vw_cliente_publico",
            "columna": "segmento",
            "valor": "BANCA MASIVA",
        },
    )

    assert "error" not in datos
    assert datos["valor"] == "Banca masiva"
    assert datos["ambiguo"] is False


def test_verificar_cifras_contra_la_ultima_consulta(backend):
    backend._autenticar_prueba("auditor")
    backend.configurar_perfil({"rol": "superusuario"})
    ejecucion = _llamar(
        backend,
        "ejecutar_consulta",
        {"sql": "SELECT COUNT(*) AS n FROM dim_cliente"},
    )
    total = ejecucion["filas"][0][0]

    valido = _llamar(
        backend,
        "verificar_cifras",
        {"texto": f"Hay {total:,} clientes."},
    )
    invalido = _llamar(
        backend,
        "verificar_cifras",
        {"texto": "Hay 9,999 clientes."},
    )

    assert valido["ok"] is True
    assert valido["cifras_no_verificadas"] == []
    assert invalido["ok"] is False
    assert invalido["cifras_no_verificadas"] == ["9999"]


def test_resolver_valor_no_expone_otros_clientes(backend):
    backend.configurar_perfil({"rol": "usuario", "cliente_id": 1})
    datos = _llamar(
        backend,
        "resolver_valor",
        {"tabla": "dim_cliente", "columna": "num_cte", "valor": "2"},
    )

    assert "error" not in datos
    assert datos["valor"] is None
    assert datos["coincidencias"] == []
