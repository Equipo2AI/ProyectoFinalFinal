from __future__ import annotations

import json

from langchain_core.documents import Document

from proyecto.agents.normatividad_rag_agent import NormatividadRAGAgent


class RetrieverConScore:
    def __init__(self, resultados):
        self.resultados = resultados
        self.llamadas = 0

    def similarity_search_with_score(self, query, k=3):
        self.llamadas += 1
        return list(self.resultados)


def _doc(texto: str, source: str = "afore.pdf", page: int = 1) -> Document:
    return Document(
        page_content=texto,
        metadata={"source": source, "page": page},
    )


def _agente(resultados) -> NormatividadRAGAgent:
    agente = NormatividadRAGAgent(k=3)
    agente._retriever = RetrieverConScore(resultados)
    agente._vector_store = None
    return agente


def _tool(agente: NormatividadRAGAgent, nombre: str):
    return next(t for t in agente._tools if t.name == nombre)


def test_recupera_evidencia_relevante_con_scores():
    agente = _agente(
        [
            (
                _doc(
                    "Afore Banamex administra las aportaciones del trabajador "
                    "para construir su fondo de ahorro para el retiro.",
                    "afore.pdf",
                ),
                0.30,
            )
        ]
    )

    fragmentos = agente._recuperar("¿Qué es una afore?")

    assert len(fragmentos) == 1
    assert fragmentos[0]["score_semantico"] == 0.30
    assert fragmentos[0]["score_hibrido"] > 0
    assert fragmentos[0]["cobertura"] > 0


def test_rechaza_termino_ausente_aunque_el_score_supere_el_minimo():
    agente = _agente(
        [
            (
                _doc(
                    "La ley protege los datos personales de las personas.",
                    "ley_datos.pdf",
                ),
                0.43,
            )
        ]
    )

    fragmentos = agente._recuperar(
        "¿Qué dice la ley sobre los datos personales en Japón?"
    )

    assert fragmentos == []
    diagnostico = agente._cache_diagnosticos[
        "¿Qué dice la ley sobre los datos personales en Japón?"
    ]
    assert "japon" in diagnostico["ausentes"]
    assert diagnostico["motivo"] == "terminos_ausentes"


def test_acepta_score_bajo_si_la_fuente_coincide():
    agente = _agente(
        [
            (
                _doc(
                    "Afore Banamex: cuenta individual para el retiro.",
                    "afore.pdf",
                ),
                0.19,
            )
        ]
    )

    fragmentos = agente._recuperar("¿Qué es una afore?")

    assert len(fragmentos) == 1
    assert fragmentos[0]["score_semantico"] == 0.19
    assert fragmentos[0]["coincidencia_fuente"] == 1.0


def test_rechaza_fragmento_que_solo_comparte_terminos_genericos():
    agente = _agente(
        [
            (
                _doc(
                    "Artículo 65. Las reclamaciones deberán presentarse dentro "
                    "del término de dos años.",
                    "ley_servicios.pdf",
                    22,
                ),
                0.53,
            ),
            (
                _doc(
                    "El plazo de trescientos sesenta y cinco días para emitir "
                    "disposiciones de carácter general.",
                    "ley_servicios.pdf",
                    54,
                ),
                0.52,
            ),
        ]
    )

    fragmentos = agente._recuperar("plazo para presentar una reclamación")

    assert len(fragmentos) == 1
    assert fragmentos[0]["pagina"] == 22
    assert fragmentos[0]["cobertura_distintiva"] == 1.0


def test_score_none_exige_coincidencia_de_fuente():
    agente = _agente(
        [
            (
                _doc(
                    "Las instituciones de financiamiento colectivo de capital "
                    "operan en países como Francia.",
                    "Ley para Regular las Sociedades de Informacion Crediticia.pdf",
                    56,
                ),
                None,
            )
        ]
    )

    fragmentos, _ = agente._filtrar_candidatos(
        "¿Cuál es la capital de Francia?",
        agente._retriever.resultados,
        "local",
    )

    assert fragmentos == []


def test_deduplica_fragmentos_repetidos():
    texto = "Artículo 1. La presente Ley es de observancia general."
    agente = _agente(
        [
            (_doc(texto, "ley.pdf", 1), 0.50),
            (_doc(texto, "ley.pdf", 1), 0.40),
        ]
    )

    fragmentos = agente._recuperar("¿Qué dice el artículo 1 de la ley?")

    assert len(fragmentos) == 1
    assert fragmentos[0]["score_semantico"] == 0.50


def test_buscar_evidencia_expone_sin_evidencia_y_diagnostico():
    agente = _agente(
        [
            (
                _doc(
                    "La ley protege los datos personales de las personas.",
                    "ley_datos.pdf",
                ),
                0.43,
            )
        ]
    )

    datos = json.loads(
        _tool(agente, "buscar_evidencia").invoke(
            {"query": "datos personales en Japón"}
        )
    )

    assert datos["sin_evidencia"] is True
    assert datos["total"] == 0
    assert "japon" in datos["diagnostico"]["ausentes"]
    assert datos["diagnostico"]["candidatos"] == 1


def test_buscar_normatividad_muestra_score_y_fuente():
    agente = _agente(
        [
            (
                _doc(
                    "Afore Banamex: cuenta individual para el retiro.",
                    "afore.pdf",
                ),
                0.30,
            )
        ]
    )

    texto = _tool(agente, "buscar_normatividad").invoke({"query": "afore"})

    assert "afore.pdf" in texto
    assert "score" in texto.lower()
    assert "semántico" in texto.lower()


def test_buscar_normatividad_y_evidencia_comparten_recuperacion():
    agente = _agente(
        [
            (
                _doc(
                    "Afore Banamex: cuenta individual para el retiro.",
                    "afore.pdf",
                ),
                0.30,
            )
        ]
    )

    _tool(agente, "buscar_normatividad").invoke({"query": "afore"})
    _tool(agente, "buscar_evidencia").invoke({"query": "afore"})

    assert agente._retriever.llamadas == 1


def test_verificar_cita_reutiliza_evidencia():
    agente = _agente(
        [
            (
                _doc(
                    "Artículo 65. Las reclamaciones deberán presentarse dentro "
                    "del término de dos años.",
                    "ley_servicios.pdf",
                    22,
                ),
                0.53,
            )
        ]
    )

    evidencia = json.loads(
        _tool(agente, "buscar_evidencia").invoke(
            {"query": "plazo para presentar una reclamación"}
        )
    )
    cita_id = evidencia["fragmentos"][0]["cita_id"]

    resultado = json.loads(
        _tool(agente, "verificar_cita").invoke(
            {
                "cita": "Las reclamaciones deberán presentarse dentro del término",
                "cita_id": cita_id,
            }
        )
    )

    assert resultado["existe"] is True
    assert resultado["coincide"] is True
