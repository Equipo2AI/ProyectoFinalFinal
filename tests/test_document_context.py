"""Pruebas del contexto documental privado, sin LLM ni servicios externos."""

from __future__ import annotations

import time
from pathlib import Path

from proyecto.memory.document_context import (
    DocumentContextConfig,
    DocumentContextStore,
)
from proyecto.orchestration.grafo import planear_rutas
from proyecto.orchestration.orquestador import Orquestador


def _store(tmp_path: Path, **overrides) -> DocumentContextStore:
    config = DocumentContextConfig(
        db_path=tmp_path / "document_context.db",
        **overrides,
    )
    return DocumentContextStore(config)


def test_contexto_persiste_por_usuario_y_no_se_mezcla(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        "ana",
        "contrato.md",
        "markdown",
        "# Contrato\n\nEl saldo inicial es 1,000.",
        "/tmp/contrato.md",
    )

    otra_instancia = _store(tmp_path)
    assert [doc["nombre"] for doc in otra_instancia.list("ana")] == ["contrato.md"]
    assert "1,000" in otra_instancia.build_context("ana", "¿Cuál es el saldo inicial?")

    assert otra_instancia.list("beto") == []
    assert otra_instancia.build_context("beto", "¿Cuál es el saldo inicial?") == ""


def test_documento_largo_envia_resumen_y_seccion_relevante(tmp_path: Path) -> None:
    texto = "# Manual\n\n" + "\n\n".join(
        f"## Sección {numero}\n"
        + ("relleno " * 80)
        + (f"dato clave {numero}" if numero == 7 else "")
        for numero in range(1, 11)
    )
    store = _store(tmp_path, max_chars=2_200, summary_chars=400)
    store.save("ana", "manual.md", "markdown", texto)

    contexto = store.build_context("ana", "¿Qué dice la sección 7?")

    assert "Resumen del documento:" in contexto
    assert "dato clave 7" in contexto
    assert len(contexto) <= 2_250


def test_documento_activo_y_seleccion_por_nombre(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("ana", "contrato_alpha.md", "markdown", "Alpha: plazo de 30 días.")
    store.save("ana", "contrato_beta.md", "markdown", "Beta: tasa de 12%.")

    activo = store.get_active("ana")
    assert activo
    assert store.get("ana", activo)["nombre"] == "contrato_beta.md"

    contexto = store.build_context("ana", "¿Qué dice contrato_alpha?")
    assert "Documento activo: contrato_alpha.md" in contexto
    assert "plazo de 30 días" in contexto
    assert store.get("ana", store.get_active("ana"))["nombre"] == "contrato_alpha.md"


def test_cuatro_documentos_no_ocultan_el_primero(tmp_path: Path) -> None:
    store = _store(tmp_path, max_chars=2_000)
    store.save("ana", "factura_enero.md", "markdown", "Factura de enero: total 100.")
    store.save("ana", "factura_febrero.md", "markdown", "Factura de febrero: total 200.")
    store.save("ana", "factura_marzo.md", "markdown", "Factura de marzo: total 300.")
    store.save("ana", "factura_abril.md", "markdown", "Factura de abril: total 400.")

    contexto = store.build_context("ana", "¿Cuál es el total de factura_enero?")

    assert "Documento activo: factura_enero.md" in contexto
    assert "total 100" in contexto
    assert "factura_febrero.md" in contexto


def test_limites_de_documentos_y_ttl(tmp_path: Path) -> None:
    store = _store(tmp_path, max_docs=2, ttl_days=1)
    store.save("ana", "uno.md", "markdown", "contenido uno")
    time.sleep(0.01)
    store.save("ana", "dos.md", "markdown", "contenido dos")
    time.sleep(0.01)
    store.save("ana", "tres.md", "markdown", "contenido tres")

    documentos = store.list("ana")
    assert len(documentos) == 2
    assert "uno.md" not in {doc["nombre"] for doc in documentos}

    with store._connect() as conexion:  # prueba deliberada del TTL
        conexion.execute(
            "UPDATE documents SET updated_at = ? WHERE user_id = ?",
            (time.time() - 2 * 86_400, "ana"),
        )
    store.purge_expired()
    assert store.list("ana") == []


def test_orquestador_recarga_contexto_tras_reiniciar(tmp_path: Path) -> None:
    config = DocumentContextConfig(db_path=tmp_path / "document_context.db")
    store = DocumentContextStore(config)
    primero = Orquestador(usuario="ana", document_store=store)
    primero.registrar_documento(
        "/tmp/contrato.md",
        "markdown",
        "# Contrato\n\nEl saldo inicial es 1,000.",
    )

    segundo = Orquestador(
        usuario="ana",
        document_store=DocumentContextStore(config),
    )
    contexto = segundo._contexto_documentos("¿Cuál es el saldo inicial?")
    assert "1,000" in contexto

    otro_usuario = Orquestador(
        usuario="beto",
        document_store=DocumentContextStore(config),
    )
    assert otro_usuario._contexto_documentos("¿Cuál es el saldo inicial?") == ""


def test_rutas_priorizan_documento_salvo_fuente_explicita() -> None:
    contexto = "Documento activo: contrato.md"

    assert planear_rutas(
        "¿Cuál es el saldo inicial?",
        ultimo_agente="document_intelligence",
        contexto_documentos=contexto,
    ) == ["directo"]
    assert planear_rutas(
        "¿Relaciona el plazo con la tasa?",
        ultimo_agente="document_intelligence",
        contexto_documentos=contexto,
    ) == ["directo"]
    assert planear_rutas(
        "¿Cuál es el correo del cliente?",
        ultimo_agente="document_intelligence",
        contexto_documentos=contexto,
    ) == ["directo"]
    assert planear_rutas(
        "¿Qué dice la normativa?",
        ultimo_agente="document_intelligence",
        contexto_documentos=contexto,
    ) == ["normatividad_rag"]
    assert planear_rutas(
        "¿Qué tablas existen en la base de datos?",
        ultimo_agente="document_intelligence",
        contexto_documentos=contexto,
    ) != ["directo"]
