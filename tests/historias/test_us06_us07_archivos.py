"""US-06 (imágenes) y US-07 (documentos): límites, formatos y conversión.

El OCR y la descripción dependen del modelo de visión y se evalúan en la
suite DeepEval; aquí se prueba lo determinista del servicio.
"""

from __future__ import annotations

import zipfile

import pytest

import proyecto.servicio as servicio
from proyecto.agents.document_intelligence_agent import document_to_markdown


def _prohibido(*args, **kwargs):
    raise AssertionError("No se debe invocar el modelo para un archivo inválido")


@pytest.fixture
def sesion_cliente(operador, monkeypatch):
    with operador.sesion("cliente_1") as token:
        monkeypatch.setenv("PROYECTO_AUTH_TOKEN", token)
        monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", _prohibido)
        yield token


def _docx(parrafos: list[str]) -> bytes:
    import io

    cuerpo = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in parrafos)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zz:
        zz.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zz.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        zz.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            f'wordprocessingml/2006/main"><w:body>{cuerpo}</w:body></w:document>',
        )
    return buffer.getvalue()


# ------------------------------------------------------------- US-06


def test_us06_ca03_formato_de_imagen_no_soportado(sesion_cliente, carga_de):
    ruta = carga_de("cliente_1", "comprobante.tiff", b"II*\x00")
    respuesta = servicio.procesar_imagen_auto(str(ruta), "Extrae el texto", "comprobante.tiff")
    assert respuesta["error"].startswith("Formato no soportado: .tiff")
    assert ".png" in respuesta["respuesta"] and "Traceback" not in respuesta["respuesta"]


def test_us06_ca03_imagen_mayor_a_10_mb(sesion_cliente, carga_de):
    ruta = carga_de("cliente_1", "grande.png", b"\0" * (10 * 1024 * 1024 + 1))
    respuesta = servicio.procesar_imagen_auto(str(ruta), "Extrae el texto", "grande.png")
    assert respuesta["error"] == "La imagen supera el límite de 10 MB."


def test_us06_ca03_fallo_del_modelo_se_informa(sesion_cliente, carga_de, monkeypatch):
    ruta = carga_de("cliente_1", "recibo.png", b"\x89PNG\r\n\x1a\n")

    class ModeloCaido:
        def invoke(self, *args, **kwargs):
            raise ConnectionError("Ollama no responde")

    monkeypatch.setattr(servicio, "ocr_image", ModeloCaido())
    respuesta = servicio.procesar_imagen(str(ruta), "ocr")
    assert respuesta["error"].startswith("No se pudo procesar la imagen")
    assert respuesta["pasos"][0]["estado"] == "error"


def test_us06_ca04_sin_operacion_pide_aclaracion(sesion_cliente, carga_de, monkeypatch):
    ruta = carga_de("cliente_1", "comprobante.png", b"\x89PNG\r\n\x1a\n")
    registradas = []

    class OrquestadorFalso:
        modelo = "falso"

        def registrar_imagen(self, ruta, pregunta):
            registradas.append(ruta)

    monkeypatch.setattr(servicio, "_get_orquestador", lambda: OrquestadorFalso())
    respuesta = servicio.procesar_imagen_auto(str(ruta), "", "comprobante.png")
    assert respuesta["respuesta"] == "¿Quieres que extraiga el texto (OCR) o que describa la imagen?"
    assert registradas == [str(ruta.resolve())]


def test_us06_ca03_imagen_fuera_de_las_cargas_del_usuario(sesion_cliente, tmp_path):
    ajena = tmp_path / "otro_usuario.png"
    ajena.write_bytes(b"\x89PNG\r\n\x1a\n")
    respuesta = servicio.procesar_imagen_auto(str(ajena), "Extrae el texto", "otro_usuario.png")
    assert respuesta["error"].startswith("Acceso denegado")


# ------------------------------------------------------------- US-07


def test_us07_ca01_docx_se_convierte_a_markdown(tmp_path):
    ruta = tmp_path / "estado.docx"
    ruta.write_bytes(_docx(["Estado de cuenta de cheques", "Saldo final: $10,329.53"]))
    texto = document_to_markdown.invoke({"file_path": str(ruta)})
    assert "Saldo final: $10,329.53" in texto


def test_us07_ca01_csv_se_convierte_a_tabla_markdown(tmp_path):
    ruta = tmp_path / "movimientos.csv"
    ruta.write_text("fecha,concepto,importe\n2026-06-21,Pago tarjeta,2902.53\n", encoding="utf-8")
    texto = document_to_markdown.invoke({"file_path": str(ruta)})
    assert "|" in texto and "2902.53" in texto


def test_us07_ca04_documento_mayor_a_25_mb(sesion_cliente, carga_de):
    ruta = carga_de("cliente_1", "grande.pdf", b"%PDF" + b"\0" * (25 * 1024 * 1024))
    respuesta = servicio.procesar_documento(str(ruta), "Resume", "grande.pdf")
    assert respuesta["error"] == "El documento supera el límite de 25 MB."


def test_us07_ca04_formato_no_soportado(sesion_cliente, carga_de):
    ruta = carga_de("cliente_1", "programa.exe", b"MZ")
    respuesta = servicio.procesar_documento(str(ruta), "Resume", "programa.exe")
    assert respuesta["error"].startswith("Formato no soportado: .exe")
    assert ".docx" in respuesta["respuesta"]


def test_us07_ca04_conversion_vacia_no_inventa(sesion_cliente, carga_de, monkeypatch):
    ruta = carga_de("cliente_1", "vacio.html", b"<html><body></body></html>")
    monkeypatch.setattr(
        servicio,
        "_llamar_mcp_document_intelligence",
        lambda *a, **k: "MarkItDown no extrajo texto de este documento.",
    )
    monkeypatch.setattr(servicio, "_get_orquestador", _prohibido)
    respuesta = servicio.procesar_documento(str(ruta), "Resume", "vacio.html")
    assert respuesta["alertas"] == ["MarkItDown no extrajo texto de este documento."]
    assert respuesta["pasos"][0]["estado"] == "alerta"
