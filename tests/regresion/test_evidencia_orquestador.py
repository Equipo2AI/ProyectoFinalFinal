from __future__ import annotations

import proyecto.orchestration.grafo as grafo
from proyecto.orchestration.grafo import _agregar_evidencia_normativa


def _resultado(evidencia: str) -> dict:
    return {
        "normatividad_rag": {
            "agente": "normatividad_rag",
            "estado": "ok",
            "respuesta": evidencia,
        }
    }


def test_agrega_evidencia_a_sintesis_multiagente_y_quita_scores():
    evidencia = (
        "Evidencia documental recuperada mediante Pinecone:\n\n"
        "FUENTE: ley.pdf, página 22 (score híbrido 0.6673, semántico 0.5339)\n"
        "Artículo 65. Las reclamaciones deberán presentarse dentro del término "
        "de dos años.\n\n---\n\n"
        "FUENTE: aviso.pdf, página 4 (score híbrido 0.7000, semántico 0.6500)\n"
        "El aviso describe el tratamiento de datos personales."
    )

    salida = _agregar_evidencia_normativa(
        "La respuesta sintetizada para el usuario.",
        _resultado(evidencia),
    )

    assert "La respuesta sintetizada para el usuario." in salida
    assert "## Evidencia normativa recuperada" in salida
    assert "FUENTE: ley.pdf, página 22\n" in salida
    assert "FUENTE: aviso.pdf, página 4\n" in salida
    assert "Artículo 65" in salida
    assert "score" not in salida
    assert salida.count("FUENTE:") == 2


def test_no_duplica_evidencia_cuando_la_respuesta_ya_es_del_rag():
    evidencia = (
        "Evidencia documental recuperada mediante Pinecone:\n\n"
        "FUENTE: ley.pdf, página 22 (score híbrido 0.6673, semántico 0.5339)\n"
        "Artículo 65. Las reclamaciones deberán presentarse dentro del término "
        "de dos años."
    )

    salida = _agregar_evidencia_normativa(evidencia, _resultado(evidencia))

    assert salida == evidencia.strip()
    assert salida.count("FUENTE:") == 1
    assert "## Evidencia normativa recuperada" not in salida


def test_declara_sin_evidencia_en_la_respuesta_final():
    evidencia = (
        "Sin evidencia documental suficiente: no se encontraron fragmentos "
        "relevantes para responder sin inventar. Términos sin coincidencia en "
        "el corpus: japon."
    )

    salida = _agregar_evidencia_normativa(
        "No tengo información sobre Japón.",
        _resultado(evidencia),
    )

    assert "No tengo información sobre Japón." in salida
    assert "## Evidencia normativa recuperada" in salida
    assert "No se recuperó evidencia suficiente en el corpus." in salida


def test_no_agrega_nada_si_no_hubo_rag():
    salida = _agregar_evidencia_normativa(
        "Respuesta de Data Analyst.",
        {"data_analyst": {"estado": "ok", "respuesta": "42"}},
    )

    assert salida == "Respuesta de Data Analyst."


def test_ignora_rag_con_error():
    salida = _agregar_evidencia_normativa(
        "Respuesta directa.",
        {
            "normatividad_rag": {
                "estado": "error",
                "respuesta": "FUENTE: ley.pdf, página 1\nError",
            }
        },
    )

    assert salida == "Respuesta directa."


def test_nodo_normatividad_usa_la_subpregunta_normativa(monkeypatch):
    capturado = {}

    def ejecutar(servidor, herramienta, argumentos, nombre_traza):
        capturado.update(argumentos)
        return "Evidencia de prueba", {"name": nombre_traza}, "ok"

    monkeypatch.setattr(grafo, "_ejecutar_mcp", ejecutar)

    grafo._nodo_normatividad(
        {
            "consulta": (
                "¿Qué dice la ley de protección de datos personales y "
                "cuántos clientes hay en la base?"
            ),
            "subpreguntas": [
                "¿Qué disposiciones establece la ley de protección de datos "
                "personales sobre el tratamiento?",
                "¿Cuántos clientes hay en la base de datos?",
            ],
        }
    )

    assert capturado["query"].startswith("¿Qué disposiciones establece la ley")


def test_nodo_sintetizar_aplica_evidencia_en_flujo_multiagente(monkeypatch):
    evidencia = (
        "Evidencia documental recuperada mediante Pinecone:\n\n"
        "FUENTE: ley.pdf, página 22 (score híbrido 0.6673, semántico 0.5339)\n"
        "Artículo 65. Las reclamaciones deberán presentarse dentro del término "
        "de dos años."
    )
    monkeypatch.setattr(
        grafo,
        "_sintetizar_cooperativo",
        lambda estado, plan, resultados: "Síntesis sin evidencia literal.",
    )

    salida = grafo._nodo_sintetizar(
        {
            "plan": ["normatividad_rag", "data_analyst"],
            "resultados": {
                **_resultado(evidencia),
                "data_analyst": {"estado": "ok", "respuesta": "1 cliente"},
            },
        }
    )["respuesta"]

    assert "Síntesis sin evidencia literal." in salida
    assert "## Evidencia normativa recuperada" in salida
    assert "FUENTE: ley.pdf, página 22" in salida
    assert "Artículo 65" in salida
    assert "score" not in salida


def test_redactar_conserva_la_evidencia_normativa(monkeypatch):
    evidencia = (
        "Evidencia documental recuperada mediante Pinecone:\n\n"
        "FUENTE: ley.pdf, página 22 (score híbrido 0.6673, semántico 0.5339)\n"
        "Artículo 65. Las reclamaciones deberán presentarse dentro del término "
        "de dos años."
    )
    monkeypatch.setattr(
        grafo,
        "redactar_respuesta",
        lambda *args, **kwargs: "Respuesta simple sin fuentes.",
    )

    salida = grafo._nodo_redactar(
        {
            "consulta": "¿Qué dice la ley sobre reclamaciones?",
            "respuesta": "La ley fija un plazo de dos años.",
            "perfil_respuesta": {
                "estilo": "simple",
                "mostrar_sql": False,
                "origen": "rol",
            },
            "trazas": [],
            "resultados": _resultado(evidencia),
            "redactada": False,
        }
    )["respuesta"]

    assert "Respuesta simple sin fuentes." in salida
    assert "## Evidencia normativa recuperada" in salida
    assert "FUENTE: ley.pdf, página 22" in salida
    assert "Artículo 65" in salida
    assert "score" not in salida
