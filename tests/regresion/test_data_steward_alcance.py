from __future__ import annotations

import json

import pytest

from proyecto.agents.data_steward_agent import (
    DataStewardAgent,
    evaluar_alcance_data_steward,
)
from proyecto.orchestration import grafo


@pytest.mark.parametrize(
    "consulta",
    [
        "¿Qué tablas y columnas tiene raw_clientes?",
        "¿Cuántas filas tiene raw_clientes?",
        "¿Qué problemas de calidad tiene raw_clientes?",
        "¿Hay duplicados en dim_cliente?",
        "¿Qué relaciones tiene fact_saldos_captacoloca_m?",
        "¿Cuál es la estructura de dim_cliente?",
        "¿Qué tipos de datos tiene la columna saldo_mes?",
        "¿Qué claves tiene raw_clientes?",
        "¿Qué datasets hay?",
        "¿Qué base de datos usas?",
        "¿Qué es un esquema de datos?",
        "¿Qué es calidad de datos?",
        "¿Cuántos nulos hay en dim_cliente?",
        "¿Hay valores negativos en saldo_mes?",
    ],
)
def test_metadatos_y_calidad_quedan_en_dominio(consulta):
    alcance = evaluar_alcance_data_steward(consulta)

    assert alcance["estado"] == "en_dominio"
    assert alcance["destino"] is None


@pytest.mark.parametrize(
    "consulta, destino",
    [
        ("¿Cuál es el saldo promedio por producto?", "data_analyst"),
        ("¿Cuántos clientes hay?", "data_analyst"),
        ("¿Cuántos clientes hay en la base de datos?", "data_analyst"),
        ("¿Cuántos clientes hay en el dataset?", "data_analyst"),
        ("¿Cuál es el total de transacciones?", "data_analyst"),
        ("¿Cuál es la tasa de interés?", "data_analyst"),
        ("¿Qué dice la ley de protección de datos?", "normatividad_rag"),
        ("¿Qué es Banamex?", "normatividad_rag"),
        ("¿Qué dice el aviso de privacidad?", "normatividad_rag"),
        ("Procesa esta factura", "document_intelligence"),
        ("¿Puedes leer este PDF?", "document_intelligence"),
        ("¿Qué es KYC?", "fuera_mvp"),
        ("¿Cómo prevenimos el lavado de dinero?", "fuera_mvp"),
        ("¿Cuál es la capital de Francia?", "directo"),
        ("¿Qué es una afore?", "directo"),
        ("Escríbeme un poema", "directo"),
    ],
)
def test_fuera_de_dominio_redirige(consulta, destino):
    alcance = evaluar_alcance_data_steward(consulta)

    assert alcance["estado"] == "fuera_de_dominio"
    assert alcance["destino"] == destino
    assert "no corresponde al Data Steward" in alcance["mensaje"]
    assert "Mi alcance" in alcance["mensaje"]


def test_datos_personales_mantiene_guard():
    alcance = evaluar_alcance_data_steward("Dame los nombres de los clientes")

    assert alcance["estado"] == "fuera_de_dominio"
    assert alcance["destino"] == "privacidad"
    assert "datos personales" in alcance["mensaje"]


def test_nombres_de_columnas_son_metadatos_no_datos_personales():
    for consulta in (
        "Dame los nombres de las columnas de raw_clientes",
        "Dame los nombres de los campos de dim_cliente",
        "¿Cuáles son los nombres de las tablas?",
    ):
        alcance = evaluar_alcance_data_steward(consulta)
        assert alcance["estado"] == "en_dominio", consulta


def test_consulta_ambigua_no_se_rechaza():
    assert evaluar_alcance_data_steward("Hola")["estado"] == "ambiguo"
    assert evaluar_alcance_data_steward("¿Puedes ayudarme?")["estado"] == "ambiguo"


def test_orquestador_rechaza_fuera_de_alcance_antes_de_steward():
    assert grafo.fuera_de_alcance("¿Cuál es la capital de Francia?") is True

    plan, subpreguntas, origen, razon = grafo.planear_rutas_hibrido(
        "¿Cuál es la capital de Francia?"
    )

    assert plan == ["directo"]
    assert subpreguntas == []
    assert origen == "reglas"
    assert razon == "fuera de alcance"


def test_procesar_rechaza_fuera_de_dominio_sin_llamar_al_llm(monkeypatch):
    agente = DataStewardAgent()

    def falla():
        raise AssertionError("no debe crear el agente LLM")

    monkeypatch.setattr(agente, "_get_agent", falla)

    respuesta = agente.procesar("¿Cuál es la capital de Francia?")

    assert "no corresponde al Data Steward" in respuesta


def test_nodo_steward_rechaza_fuera_de_dominio_sin_mcp(monkeypatch):
    def falla(*args, **kwargs):
        raise AssertionError("no debe llamar MCP")

    monkeypatch.setattr(grafo, "_ejecutar_mcp", falla)

    estado = grafo._nodo_data_steward(
        {"consulta": "¿Cuál es el saldo promedio por producto?"}
    )

    resultado = estado["resultados"]["data_steward"]
    assert resultado["estado"] == "fuera_de_alcance"
    assert "Data Analyst" in resultado["respuesta"]
    assert estado["trazas"][0]["name"] == "alcance_data_steward"


def test_nodo_steward_consulta_metadatos_sigue_usando_mcp(monkeypatch):
    llamadas = []

    def falso_mcp(servidor, herramienta, argumentos, nombre_traza):
        llamadas.append((servidor, herramienta, argumentos))
        datos = {
            "tabla": "raw_clientes",
            "es_vista": False,
            "filas": 10,
            "columnas": [
                {
                    "nombre": "id",
                    "tipo": "INTEGER",
                    "valores_faltantes": 0,
                }
            ],
            "relaciones": [],
            "catalogo_tablas": ["raw_clientes"],
        }
        return (
            json.dumps(datos),
            {
                "name": nombre_traza,
                "args": argumentos,
                "salida": "ok",
                "estado": "ok",
            },
            "ok",
        )

    monkeypatch.setattr(grafo, "_ejecutar_mcp", falso_mcp)

    estado = grafo._nodo_data_steward(
        {"consulta": "¿Qué columnas tiene raw_clientes?"}
    )

    assert llamadas == [
        ("data_steward_server", "inspeccionar_dataset", {"tabla": "raw_clientes"})
    ]
    resultado = estado["resultados"]["data_steward"]
    assert resultado["estado"] == "ok"
    assert "raw_clientes" in resultado["respuesta"]
