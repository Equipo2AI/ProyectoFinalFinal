"""US-08 · Hacer preguntas de seguimiento sobre el archivo (P1)."""

from __future__ import annotations

import pytest

ESTADO = "Estado de cuenta 100000\nFecha de corte: 30 de junio de 2026\nSaldo final: $10,329.53"


@pytest.fixture
def orquestador(almacen_documentos):
    from proyecto.orchestration.orquestador import Orquestador

    return Orquestador(usuario="cliente_1", document_store=almacen_documentos)


def test_ca01_el_seguimiento_recibe_el_texto_del_archivo(orquestador, tmp_path):
    orquestador.registrar_documento(str(tmp_path / "estado.docx"), "markdown", ESTADO, "estado.docx")
    contexto = orquestador._contexto_documentos("¿Cuál es el saldo final?")
    assert "Saldo final: $10,329.53" in contexto
    assert "Documento activo: estado.docx" in contexto


def test_ca04_otra_identidad_no_recibe_el_documento(almacen_documentos):
    almacen_documentos.save("cliente_1", "estado.docx", "markdown", ESTADO, "/tmp/estado.docx")
    assert almacen_documentos.build_context("cliente_2", "saldo final") == ""
    assert almacen_documentos.list("cliente_2") == []


def test_ca04_la_memoria_de_largo_plazo_esta_desactivada():
    from proyecto.memory.long_term import config_from_file

    assert config_from_file().enabled is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Orquestador.reiniciar() (botón Limpiar) vacía _documentos, pero "
        "_contexto_documentos() vuelve a leer DocumentContextStore, que "
        "conserva el documento del usuario hasta su TTL. Corrección sugerida: "
        "que reiniciar() llame a document_store.clear_active() y borre los "
        "documentos de la sesión."
    ),
)
def test_ca03_limpiar_descarta_el_contexto_documental(orquestador, tmp_path):
    orquestador.registrar_documento(str(tmp_path / "estado.docx"), "markdown", ESTADO, "estado.docx")
    orquestador.reiniciar()
    assert orquestador._contexto_documentos("¿Cuál es el saldo final?") == ""
