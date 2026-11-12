"""Pruebas del lector de PDF sin cargar el modelo de visión."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib.backends.backend_pdf import PdfPages

from proyecto.agents.document_intelligence_agent import document_to_markdown
from proyecto.agents.pdf_reader import _parse_paginas, pdf_to_markdown
from proyecto import servicio
from proyecto.servicio import _paginas_solicitadas


def _pdf_texto(path: Path) -> None:
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(
            0.1,
            0.7,
            "Contrato de prueba\nEl saldo inicial es 1,000 pesos.\n"
            "La tasa anual es 12 por ciento.",
        )
        pdf.savefig(fig)
        plt.close(fig)


def _pdf_imagen(path: Path, paginas: int = 1) -> None:
    with PdfPages(path) as pdf:
        for _ in range(paginas):
            fig = plt.figure(figsize=(8.27, 11.69))
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(np.zeros((120, 120, 3)))
            ax.axis("off")
            pdf.savefig(fig)
            plt.close(fig)


def test_pdf_con_texto_usa_capa_nativa(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "contrato.pdf"
    _pdf_texto(pdf)

    def fallar_ocr(*args, **kwargs):
        raise AssertionError("no debe llamar al OCR si hay texto nativo")

    monkeypatch.setattr("proyecto.agents.pdf_reader._ocr_pagina", fallar_ocr)

    resultado = pdf_to_markdown(str(pdf))

    assert "## Página 1" in resultado
    assert "saldo inicial" in resultado
    assert "OCR local" not in resultado


def test_pdf_sin_texto_usa_ocr_local(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "escaneado.pdf"
    _pdf_imagen(pdf)
    llamadas: list[int] = []

    def ocr_falso(page, numero, modelo, prompt, directorio):
        llamadas.append(numero)
        return "Texto transcrito por OCR"

    monkeypatch.setattr("proyecto.agents.pdf_reader._ocr_pagina", ocr_falso)

    resultado = pdf_to_markdown(str(pdf), modelo="qwen2.5vl:3b")

    assert llamadas == [1]
    assert "## Página 1" in resultado
    assert "OCR local" in resultado
    assert "qwen2.5vl:3b" in resultado
    assert "Texto transcrito por OCR" in resultado


def test_limite_de_paginas_ocr(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "escaneado_largo.pdf"
    _pdf_imagen(pdf, paginas=3)
    monkeypatch.setenv("DOCUMENT_PDF_OCR_MAX_PAGES", "1")

    def ocr_falso(page, numero, modelo, prompt, directorio):
        return f"OCR {numero}"

    monkeypatch.setattr("proyecto.agents.pdf_reader._ocr_pagina", ocr_falso)

    resultado = pdf_to_markdown(str(pdf))

    assert "OCR 1" in resultado
    assert "OCR 2" not in resultado
    assert "Se omitieron 2 páginas" in resultado
    assert "2-3" in resultado


def test_tool_document_to_markdown_delega_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "contrato.pdf"
    _pdf_texto(pdf)

    resultado = document_to_markdown.invoke({"file_path": str(pdf), "modo": "texto"})

    assert "## Página 1" in resultado
    assert "saldo inicial" in resultado


def test_rangos_de_paginas() -> None:
    assert _parse_paginas("1-3,5", 5) == [1, 2, 3, 5]
    assert _parse_paginas("5-3", 5) == [3, 4, 5]
    assert _parse_paginas("", 2) == [1, 2]
    assert _parse_paginas("0,9", 5) == []
    assert _parse_paginas("1-3,5-7", 10) == [1, 2, 3, 5, 6, 7]


def test_rango_de_paginas_en_la_consulta() -> None:
    assert _paginas_solicitadas("Lee las páginas 3 a 5") == "3-5"
    assert _paginas_solicitadas("procesa página 7") == "7"
    assert _paginas_solicitadas("páginas 1-5 y 8") == "1-5,8"
    assert _paginas_solicitadas("analiza el documento") == ""


def test_procesar_documento_pasa_rango_de_paginas(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "contrato.pdf"
    _pdf_texto(pdf)
    llamada: dict[str, object] = {}

    def mcp_falso(nombre, ruta, campo="image_path", extra=None):
        llamada.update({"nombre": nombre, "ruta": ruta, "campo": campo, "extra": extra})
        return "## Página 3\n\nContenido"

    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", mcp_falso)
    monkeypatch.setattr(
        servicio,
        "_responder_con_archivo",
        lambda *args, **kwargs: {"respuesta": "ok"},
    )

    resultado = servicio.procesar_documento(
        str(pdf), "Lee las páginas 3 a 5", "contrato.pdf"
    )

    assert resultado == {"respuesta": "ok"}
    assert llamada["campo"] == "file_path"
    assert llamada["extra"] == {"modo": "auto", "paginas": "3-5"}


def test_pdf_inexistente_devuelve_error(tmp_path: Path) -> None:
    resultado = pdf_to_markdown(str(tmp_path / "no_existe.pdf"))

    assert resultado.startswith("No se pudo convertir el documento a Markdown:")
    assert "No existe el PDF" in resultado


def test_modo_texto_no_hace_ocr(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "escaneado.pdf"
    _pdf_imagen(pdf)

    def fallar_ocr(*args, **kwargs):
        raise AssertionError("modo texto no debe hacer OCR")

    monkeypatch.setattr("proyecto.agents.pdf_reader._ocr_pagina", fallar_ocr)

    resultado = pdf_to_markdown(str(pdf), modo="texto")

    assert "La página no contiene capa de texto" in resultado
