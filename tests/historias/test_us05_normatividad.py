"""US-05 · Responder sobre normativa con evidencia y referencias (P0).

Se fuerza la caída de Pinecone bloqueando la conexión de red, así que estas
pruebas usan solo el corpus local de ``data_governance_files/``.
"""

from __future__ import annotations

import json
import socket
import unicodedata

import pytest

def _nfc(texto: str) -> str:
    """macOS guarda nombres de archivo en NFD; se compara en NFC."""
    return unicodedata.normalize("NFC", texto)


TERMINOS = "¿Qué indican los términos y condiciones Citibanamex sobre el acceso al Portal?"


@pytest.fixture
def agente_sin_pinecone(monkeypatch):
    def sin_red(*args, **kwargs):
        raise OSError("red deshabilitada en la prueba")

    monkeypatch.setattr(socket, "create_connection", sin_red)
    monkeypatch.setenv("PINECONE_API_KEY", "")
    monkeypatch.setenv("INDEX_NAME", "")
    from proyecto.agents.normatividad_rag_agent import NormatividadRAGAgent

    return NormatividadRAGAgent()


def _herramienta(agente, nombre: str):
    return next(t for t in agente._tools if t.name == nombre)


# ------------------------------------------------------- CA-01 / CA-04


def test_ca04_sin_indice_remoto_usa_corpus_local_con_fuente(agente_sin_pinecone):
    salida = _nfc(agente_sin_pinecone.procesar(TERMINOS))
    assert salida.startswith("Respuesta basada en fragmentos literales del corpus local")
    assert "Pinecone no disponible" in salida
    assert "mediante Pinecone" not in salida
    assert "FUENTE: términos y condiciones citibanamex.txt, página 1" in salida


def test_ca01_las_fuentes_llegan_al_contrato_de_la_ui(agente_sin_pinecone):
    from proyecto.servicio import _fuentes_de_salida

    fuentes = _fuentes_de_salida(agente_sin_pinecone.procesar(TERMINOS))
    assert any(_nfc(f).startswith("términos y condiciones citibanamex.txt, página 1") for f in fuentes)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "local_documents.retrieve elige archivos por coincidencia con el título; "
        "el título largo del aviso de privacidad nunca gana y 'privacidad' "
        "queda como término ausente en modo local."
    ),
)
def test_ca04_aviso_de_privacidad_disponible_en_modo_local(agente_sin_pinecone):
    salida = agente_sin_pinecone.procesar(
        "¿Qué dice el aviso de privacidad de Banamex sobre el tratamiento de datos personales?"
    )
    assert "AVISO DE PRIVACIDAD" in salida


# ------------------------------------------------------------------ CA-02


def test_ca02_sin_evidencia_lo_declara(agente_sin_pinecone):
    salida = agente_sin_pinecone.procesar("¿Qué dice la ley de criptomonedas de Japón sobre los exchanges?")
    assert salida.startswith("Sin evidencia documental suficiente")
    assert "japon" in salida
    assert "FUENTE:" not in salida


def test_ca02_buscar_evidencia_marca_sin_evidencia(agente_sin_pinecone):
    datos = json.loads(_herramienta(agente_sin_pinecone, "buscar_evidencia").invoke(
        {"query": "¿Qué dice la ley de criptomonedas de Japón?"}
    ))
    assert datos["sin_evidencia"] is True and datos["fragmentos"] == []


# ------------------------------------------------------------------ CA-03


def test_ca03_cita_literal_se_verifica_contra_lo_recuperado(agente_sin_pinecone):
    agente_sin_pinecone.procesar(TERMINOS)  # deja la evidencia en caché
    verificar = _herramienta(agente_sin_pinecone, "verificar_cita")
    literal = "deberá abstenerse de acceder o utilizar el Portal"
    resultado = json.loads(verificar.invoke({"cita": literal, "query": TERMINOS}))
    assert resultado["existe"] is True
    assert _nfc(resultado["fuente"]) == "términos y condiciones citibanamex.txt"


def test_ca03_cita_inventada_no_se_valida(agente_sin_pinecone):
    agente_sin_pinecone.procesar(TERMINOS)
    verificar = _herramienta(agente_sin_pinecone, "verificar_cita")
    resultado = json.loads(verificar.invoke({
        "cita": "Banamex reembolsará cualquier daño en un plazo de 5 días",
        "query": TERMINOS,
    }))
    assert resultado["existe"] is False
    assert resultado["fragmentos_revisados"] >= 1


def test_ca03_cita_id_ajeno_a_la_sesion_se_rechaza(agente_sin_pinecone):
    verificar = _herramienta(agente_sin_pinecone, "verificar_cita")
    resultado = json.loads(verificar.invoke({"cita": "cualquier texto", "cita_id": "inventado-1"}))
    assert resultado["existe"] is False and resultado["error"] == "cita_id_no_en_cache"
