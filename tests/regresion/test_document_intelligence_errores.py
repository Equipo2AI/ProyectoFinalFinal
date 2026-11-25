from __future__ import annotations

import importlib

import pytest

import proyecto.servicio as servicio
from proyecto.agents.document_intelligence_agent import (
    EXTENSIONES_DOCUMENTO,
    EXTENSIONES_IMAGEN,
    document_to_markdown,
    ocr_image,
)

agente_mod = importlib.import_module("proyecto.agents.document_intelligence_agent")


def _prohibir_mcp(*args, **kwargs):
    raise AssertionError("No se debe llamar al MCP para un archivo inválido")


def _sin_control_de_acceso(monkeypatch, modulo=servicio):
    """Dobles de autorizacion para errores de formato; no certifican seguridad."""
    from pathlib import Path
    from contextlib import nullcontext
    from proyecto.security import document_access as access, local_auth as auth
    monkeypatch.setattr(modulo, "authorize_file_access", lambda path, **k: Path(path))
    monkeypatch.setattr(auth, 'authenticated', lambda token: nullcontext())
    monkeypatch.setattr(auth, 'principal', lambda *a: {'permissions': [], 'subject': 'fixture'})
    monkeypatch.setattr(access, '_document_io', lambda *a, **k: nullcontext())
    monkeypatch.setattr(access, 'authorize_file', lambda path: ('doc-simulado', {'path': str(path)}))
    monkeypatch.setattr(servicio, '_identidad_documental', lambda *a: ('scope-simulado', 'token-simulado'))


def _assert_error_legible(respuesta, formato: str):
    assert respuesta["error"] == respuesta["respuesta"]
    assert f"Formato no soportado: {formato}" in respuesta["respuesta"]
    assert "ValueError" not in respuesta["respuesta"]
    assert "Traceback" not in respuesta["respuesta"]
    assert respuesta["pasos"][0]["estado"] == "error"
    assert respuesta["herramientas"] == []
    assert respuesta["fuentes"]


def test_procesar_imagen_rechaza_formato_sin_llamar_mcp(tmp_path, monkeypatch):
    archivo = tmp_path / "factura.exe"
    archivo.write_bytes(b"no es una imagen")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_imagen(str(archivo))

    _assert_error_legible(respuesta, ".exe")
    for extension in EXTENSIONES_IMAGEN:
        assert extension in respuesta["respuesta"]
    assert respuesta["fuentes"] == ["factura.exe"]


def test_procesar_imagen_auto_rechaza_formato_sin_orquestador(
    tmp_path, monkeypatch
):
    archivo = tmp_path / "recibo.txt"
    archivo.write_bytes(b"no es una imagen")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    def prohibir_orquestador(*args, **kwargs):
        raise AssertionError("No se debe consultar al orquestador")

    monkeypatch.setattr(servicio, "_get_orquestador", prohibir_orquestador)

    respuesta = servicio.procesar_imagen_auto(
        str(archivo), "describe esta imagen"
    )

    _assert_error_legible(respuesta, ".txt")
    for extension in EXTENSIONES_IMAGEN:
        assert extension in respuesta["respuesta"]
    assert respuesta["fuentes"] == ["recibo.txt"]


def test_procesar_documento_rechaza_formato_sin_llamar_mcp(tmp_path, monkeypatch):
    archivo = tmp_path / "contrato.exe"
    archivo.write_bytes(b"no es un documento")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_documento(str(archivo))

    _assert_error_legible(respuesta, ".exe")
    for extension in EXTENSIONES_DOCUMENTO:
        assert extension in respuesta["respuesta"]
    assert respuesta["fuentes"] == ["contrato.exe"]


def test_procesar_imagen_rechaza_archivo_demasiado_grande(tmp_path, monkeypatch):
    archivo = tmp_path / "foto.png"
    archivo.write_bytes(b"xx")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "MAX_IMAGE_BYTES", 1)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_imagen(str(archivo))

    assert respuesta["error"] == "La imagen supera el límite de 10 MB."
    assert "Traceback" not in respuesta["respuesta"]
    assert respuesta["pasos"][0]["estado"] == "error"
    assert respuesta["herramientas"] == []


def test_procesar_documento_rechaza_archivo_demasiado_grande(
    tmp_path, monkeypatch
):
    archivo = tmp_path / "contrato.pdf"
    archivo.write_bytes(b"xx")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "MAX_DOCUMENT_BYTES", 1)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_documento(str(archivo))

    assert respuesta["error"] == "El documento supera el límite de 25 MB."
    assert "Traceback" not in respuesta["respuesta"]
    assert respuesta["pasos"][0]["estado"] == "error"
    assert respuesta["herramientas"] == []


def test_procesar_imagen_inexistente_devuelve_error_controlado(
    tmp_path, monkeypatch
):
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_imagen(str(tmp_path / "no-existe.png"))

    assert respuesta["error"].startswith("No existe la imagen:")
    assert "Traceback" not in respuesta["respuesta"]
    assert respuesta["pasos"][0]["estado"] == "error"


def test_procesar_documento_inexistente_devuelve_error_controlado(
    tmp_path, monkeypatch
):
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = servicio.procesar_documento(str(tmp_path / "no-existe.pdf"))

    assert respuesta["error"].startswith("No existe el documento:")
    assert "Traceback" not in respuesta["respuesta"]
    assert respuesta["pasos"][0]["estado"] == "error"


def test_ocr_image_devuelve_error_legible_sin_clase_de_excepcion(
    tmp_path, monkeypatch
):
    _sin_control_de_acceso(monkeypatch, agente_mod)
    archivo = tmp_path / "factura.exe"
    archivo.write_bytes(b"no es una imagen")

    texto = ocr_image.invoke({"image_path": str(archivo)})

    assert "No se pudo procesar la imagen:" in texto
    assert "Formato no soportado: .exe" in texto
    assert "ValueError" not in texto
    assert "Traceback" not in texto


def test_document_to_markdown_devuelve_error_legible_sin_clase_de_excepcion(
    tmp_path, monkeypatch
):
    _sin_control_de_acceso(monkeypatch, agente_mod)
    archivo = tmp_path / "contrato.exe"
    archivo.write_bytes(b"no es un documento")

    texto = document_to_markdown.invoke({"file_path": str(archivo)})

    assert "No se pudo convertir el documento a Markdown:" in texto
    assert "Formato no soportado: .exe" in texto
    assert "ValueError" not in texto
    assert "Traceback" not in texto


@pytest.mark.parametrize(
    "modulo, funcion, extensiones",
    [
        (servicio, "procesar_imagen", EXTENSIONES_IMAGEN),
        (servicio, "procesar_documento", EXTENSIONES_DOCUMENTO),
    ],
)
def test_mensaje_de_formato_lista_todas_las_extensiones(
    tmp_path, monkeypatch, modulo, funcion, extensiones
):
    archivo = tmp_path / "archivo.bin"
    archivo.write_bytes(b"x")
    _sin_control_de_acceso(monkeypatch)
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibir_mcp)

    respuesta = getattr(modulo, funcion)(str(archivo))

    for extension in extensiones:
        assert extension in respuesta["respuesta"]
