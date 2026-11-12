"""Pruebas de evidencia cruda y verificación de citas, sin Pinecone ni LLM."""

from __future__ import annotations

import json

from langchain_core.documents import Document

from proyecto.agents.normatividad_rag_agent import NormatividadRAGAgent


class RetrieverFalso:
    def __init__(self, docs):
        self.docs = docs
        self.llamadas = 0

    def invoke(self, query):
        self.llamadas += 1
        return list(self.docs)


def _agente():
    agente = NormatividadRAGAgent()
    agente._retriever = RetrieverFalso(
        [
            Document(
                page_content=(
                    "Artículo 12. El cliente puede solicitar la cancelación de su "
                    "cuenta en cualquier momento."
                ),
                metadata={"source": "manual_cuentas.pdf", "page": 4},
            ),
            Document(
                page_content=(
                    "Artículo 13. La cancelación no procede si existen adeudos "
                    "pendientes."
                ),
                metadata={"source": "manual_cuentas.pdf", "page": 5},
            ),
        ]
    )
    return agente


def _tool(agente, nombre):
    return next(t for t in agente._tools if t.name == nombre)


def test_buscar_evidencia_devuelve_fragmentos_crudos():
    agente = _agente()
    datos = json.loads(
        _tool(agente, "buscar_evidencia").invoke({"query": "cancelar cuenta"})
    )

    assert datos["total"] == 2
    assert datos["fragmentos"][0]["fuente"] == "manual_cuentas.pdf"
    assert datos["fragmentos"][0]["pagina"] == 4
    assert datos["fragmentos"][0]["cita_id"]
    assert "Artículo 12" in datos["fragmentos"][0]["texto"]


def test_verificar_cita_encuentra_texto_literal():
    agente = _agente()
    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "El cliente puede solicitar la cancelación de su cuenta",
                "query": "cancelar cuenta",
            }
        )
    )

    assert resultado["existe"] is True
    assert resultado["coincide"] is True
    assert resultado["pagina"] == 4


def test_verificar_cita_rechaza_texto_inventado():
    agente = _agente()
    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "El banco devuelve el saldo en 24 horas.",
                "query": "cancelar cuenta",
            }
        )
    )

    assert resultado["existe"] is False
    assert resultado["fragmentos_revisados"] == 2


def test_verificar_cita_usa_cache_por_cita_id():
    agente = _agente()
    evidencia = json.loads(
        _tool(agente, "buscar_evidencia").invoke({"query": "cancelar cuenta"})
    )
    cita_id = evidencia["fragmentos"][1]["cita_id"]

    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "no procede si existen adeudos pendientes",
                "cita_id": cita_id,
            }
        )
    )

    assert resultado["existe"] is True
    assert resultado["pagina"] == 5


def test_buscar_normatividad_conserva_compatibilidad():
    agente = _agente()
    texto = _tool(agente, "buscar_normatividad").invoke({"query": "cancelar"})

    assert "FUENTE: manual_cuentas.pdf, página 4" in texto
    assert "Artículo 12" in texto


def test_buscar_normatividad_y_evidencia_comparten_recuperacion():
    agente = _agente()

    _tool(agente, "buscar_normatividad").invoke({"query": "cancelar"})
    _tool(agente, "buscar_evidencia").invoke({"query": "cancelar"})

    assert agente._retriever.llamadas == 1


def test_verificar_cita_reutiliza_query_cacheada():
    agente = _agente()

    _tool(agente, "buscar_normatividad").invoke({"query": "cancelar"})
    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "El cliente puede solicitar la cancelación de su cuenta",
                "query": "cancelar",
            }
        )
    )

    assert resultado["existe"] is True
    assert agente._retriever.llamadas == 1


def test_verificar_cita_con_cita_id_invalido_no_repite_recuperacion():
    agente = _agente()

    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "El banco devuelve el saldo en 24 horas.",
                "cita_id": "inexistente",
            }
        )
    )

    assert resultado["existe"] is False
    assert resultado["error"] == "cita_id_no_en_cache"
    assert agente._retriever.llamadas == 0
