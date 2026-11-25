"""Lectura local de PDF: capa de texto y OCR por página con Qwen2.5-VL.

El PDF nunca se envía a un servicio externo. Las páginas que no tienen texto
suficiente se rasterizan a PNG temporal y se transcriben con el modelo de visión
local. El resultado conserva marcas ``## Página N`` para que el contexto
documental privado pueda citar la página correcta.
"""

from __future__ import annotations

import os
import re
import tempfile
from contextvars import ContextVar
from pathlib import Path

from proyecto.config.models import modelo_pdf

_PREFIJO_ERROR = "No se pudo convertir el documento a Markdown:"
_MAX_PDF_BYTES = 25 * 1024 * 1024
_MAX_MARKDOWN_CHARS = 200_000
_MODOS = {"auto", "texto", "ocr", "describir"}
_PDF_SOURCE = ContextVar('authorized_pdf_source', default='')


def _env_int(nombre: str, defecto: int, minimo: int = 1) -> int:
    try:
        return max(minimo, int(str(os.getenv(nombre, defecto)).strip()))
    except (TypeError, ValueError):
        return defecto


def _modelo_pdf(modelo: str | None = None) -> str:
    return modelo or modelo_pdf()


def _error(detalle: str) -> str:
    detalle = (detalle or "error desconocido").strip()
    return f"{_PREFIJO_ERROR} {detalle}"


def _resolver_pdf(ruta: str) -> Path:
    if not (ruta or "").strip():
        raise ValueError("Falta la ruta del PDF.")
    path = Path(ruta).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe el PDF: {ruta}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"El archivo no es un PDF: {path.suffix or 'sin extensión'}")
    if path.stat().st_size > _MAX_PDF_BYTES:
        raise ValueError("El PDF supera el límite de 25 MB.")
    return path


def _parse_paginas(paginas: str | None, total: int) -> list[int]:
    if total <= 0:
        return []
    if not (paginas or "").strip():
        return list(range(1, total + 1))

    seleccion: set[int] = set()
    for tramo in str(paginas).split(","):
        tramo = tramo.strip()
        if not tramo:
            continue
        if "-" in tramo:
            inicio, fin = tramo.split("-", 1)
            try:
                desde, hasta = int(inicio), int(fin)
            except ValueError as error:
                raise ValueError(f"Rango de páginas inválido: {tramo}") from error
            if desde > hasta:
                desde, hasta = hasta, desde
            seleccion.update(range(desde, hasta + 1))
        else:
            try:
                seleccion.add(int(tramo))
            except ValueError as error:
                raise ValueError(f"Página inválida: {tramo}") from error
    return [numero for numero in sorted(seleccion) if 1 <= numero <= total]


def _texto_pagina(page) -> str:
    try:
        texto = page.get_textpage().get_text_range()
    except Exception:  # noqa: BLE001 - una página dañada no debe tirar todo el PDF
        return ""
    return (texto or "").strip()


def _ocr_pagina(page, numero: int, modelo: str, prompt: str, directorio: Path) -> str:
    """Rasteriza una página y la envía al modelo de visión local."""
    from proyecto.agents.document_intelligence_agent import _vision
    from proyecto.security.document_access import authorize_file
    authorize_file(_PDF_SOURCE.get())  # antes incluso de rasterizar la pagina

    dpi = _env_int("DOCUMENT_PDF_DPI", 150, 72)
    escala = dpi / 72
    imagen = page.render(scale=escala).to_pil()
    ruta = directorio / f"pagina_{numero:04d}.png"
    imagen.save(ruta, format="PNG")
    # La referencia padre se instala solo dentro de pdf_to_markdown autorizado.
    authorize_file(_PDF_SOURCE.get())
    result = _vision(prompt, str(ruta), modelo)
    authorize_file(_PDF_SOURCE.get())
    return result


def _nota_omision(paginas: list[int], maximo: int) -> str:
    if not paginas:
        return ""
    rangos: list[str] = []
    inicio = anterior = paginas[0]
    for numero in paginas[1:]:
        if numero == anterior + 1:
            anterior = numero
            continue
        rangos.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
        inicio = anterior = numero
    rangos.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
    return (
        f"> Se omitieron {len(paginas)} páginas sin texto por el límite de "
        f"{maximo} páginas OCR: {', '.join(rangos)}. "
        "Vuelve a procesar el PDF indicando un rango, por ejemplo «páginas 21-30»."
    )


def pdf_to_markdown(file_path: str, modo: str = 'auto', paginas: str | None = None, modelo: str | None = None) -> str:
    """La ruta publica tambien exige registro de sesion, incluso fuera de MCP."""
    from proyecto.security.local_auth import principal
    from proyecto.security.document_access import authorize_file, _document_io
    principal()
    with _document_io():
        identifier, entry = authorize_file(file_path)
        source = _PDF_SOURCE.set(identifier)
        try:
            result = _pdf_to_markdown(entry['path'], modo, paginas, modelo)
            authorize_file(identifier)
            return result
        finally:
            _PDF_SOURCE.reset(source)


def _pdf_to_markdown(
    file_path: str,
    modelo: str | None = None,
    modo: str = "auto",
    paginas: str | None = None,
) -> str:
    """Convierte un PDF a Markdown usando texto nativo y OCR local.

    ``modo``:
    - ``auto``: usa la capa de texto; si una página no tiene texto suficiente,
      la rasteriza y aplica OCR.
    - ``texto``: solo capa de texto, sin OCR.
    - ``ocr``: fuerza OCR en todas las páginas seleccionadas.
    - ``describir``: rasteriza y describe cada página; útil para gráficos.
    """
    try:
        path = _resolver_pdf(file_path)
    except Exception as error:  # noqa: BLE001 - se devuelve al agente como texto
        return _error(f"{type(error).__name__}: {error}")

    modo = (modo or "auto").strip().lower()
    if modo not in _MODOS:
        modo = "auto"
    modelo = _modelo_pdf(modelo)
    max_ocr = _env_int("DOCUMENT_PDF_OCR_MAX_PAGES", 20, 1)
    min_texto = _env_int("DOCUMENT_PDF_TEXT_MIN_CHARS", 20, 1)

    try:
        import pypdfium2 as pdfium
    except Exception as error:  # noqa: BLE001
        return _error(f"falta pypdfium2: {type(error).__name__}: {error}")

    try:
        documento = pdfium.PdfDocument(str(path))
    except Exception as error:  # noqa: BLE001
        return _error(
            "no se pudo abrir el PDF "
            f"(¿está protegido con contraseña?): {type(error).__name__}: {error}"
        )

    try:
        total = len(documento)
        try:
            seleccion = _parse_paginas(paginas, total)
        except ValueError as error:
            return _error(str(error))
        if not seleccion:
            return _error("no se seleccionaron páginas válidas.")

        if modo == "describir":
            from proyecto.agents.document_intelligence_agent import PROMPT_DESCRIPCION

            prompt = PROMPT_DESCRIPCION
        else:
            from proyecto.agents.document_intelligence_agent import PROMPT_OCR

            prompt = PROMPT_OCR

        partes: list[str] = []
        omitidas: list[int] = []
        ocr_usadas = 0
        caracteres = 0

        with tempfile.TemporaryDirectory(prefix="pdf_ocr_") as temporal:
            directorio = Path(temporal)
            for numero in seleccion:
                page = documento[numero - 1]
                texto = _texto_pagina(page)
                texto_compacto = re.sub(r"\s+", "", texto)
                necesita_ocr = modo in {"ocr", "describir"} or (
                    modo == "auto" and len(texto_compacto) < min_texto
                )

                if necesita_ocr:
                    if ocr_usadas >= max_ocr:
                        omitidas.append(numero)
                        continue
                    try:
                        contenido = _ocr_pagina(
                            page, numero, modelo, prompt, directorio
                        )
                    except Exception as error:  # noqa: BLE001 - página aislada
                        contenido = (
                            f"[No se pudo aplicar OCR a la página {numero}: "
                            f"{type(error).__name__}: {error}]"
                        )
                    ocr_usadas += 1
                    encabezado = (
                        f"## Página {numero}\n"
                        f"> Fuente: {'descripción visual' if modo == 'describir' else 'OCR local'} "
                        f"({modelo})\n\n"
                    )
                else:
                    contenido = texto or "[La página no contiene capa de texto.]"
                    encabezado = f"## Página {numero}\n\n"

                bloque = f"{encabezado}{contenido}".strip()
                if caracteres + len(bloque) > _MAX_MARKDOWN_CHARS:
                    partes.append(
                        "> [Contenido truncado: el PDF supera "
                        f"{_MAX_MARKDOWN_CHARS:,} caracteres.]"
                    )
                    break
                partes.append(bloque)
                caracteres += len(bloque) + 2

        if omitidas:
            partes.append(_nota_omision(omitidas, max_ocr))
        if not partes:
            return _error("el PDF no produjo contenido legible.")
        return "\n\n".join(partes).strip()
    finally:
        try:
            documento.close()
        except Exception:  # noqa: BLE001 - cierre best-effort
            pass
