"""Servidor MCP de imágenes y documentos.

Expone OCR, descripción de imágenes y conversión de documentos a Markdown
para que el orquestador principal pueda usar el agente sin cargarlo
directamente.

Todas las salidas pasan por el filtro de relevancia: si el archivo no está
relacionado con banca o finanzas, la tool devuelve un mensaje de rechazo
(``Documento fuera de alcance: ...``) en lugar del contenido. Como el grafo,
el hub y el servicio de la UI llaman a este servidor, el filtro aplica a
todos los modos de orquestación.
"""

from pathlib import Path

from fastmcp import FastMCP

from proyecto.agents.document_intelligence_agent import (
    describe_an_image,
    document_to_markdown,
    ocr_image,
)
from proyecto.agents.document_relevance import filtrar_documento

mcp = FastMCP("Document Intelligence MCP")


def _nombre(ruta: str) -> str:
    return Path(str(ruta or "")).name


@mcp.tool()
def extraer_texto_imagen(image_path: str) -> str:
    """Extrae el texto visible de una imagen local (factura, recibo, comprobante).

    Si la imagen no está relacionada con banca o finanzas, devuelve un mensaje
    que empieza con "Documento fuera de alcance:".
    """
    texto = ocr_image.invoke({"image_path": image_path})
    return filtrar_documento(texto, _nombre(image_path))


@mcp.tool()
def describir_imagen(image_path: str) -> str:
    """Describe una imagen local y resume sus datos visibles si es un comprobante.

    Si la imagen no está relacionada con banca o finanzas, devuelve un mensaje
    que empieza con "Documento fuera de alcance:".
    """
    texto = describe_an_image.invoke({"image_path": image_path})
    return filtrar_documento(texto, _nombre(image_path))


@mcp.tool()
def convertir_documento_markdown(
    file_path: str,
    modo: str = "auto",
    paginas: str = "",
) -> str:
    """Convierte un documento local a Markdown.

    En PDF extrae el texto nativo y aplica OCR local a las páginas escaneadas.
    Si el documento no está relacionado con banca o finanzas, devuelve un
    mensaje que empieza con "Documento fuera de alcance:".
    """
    argumentos: dict[str, str] = {"file_path": file_path, "modo": modo or "auto"}
    if (paginas or "").strip():
        argumentos["paginas"] = paginas
    texto = document_to_markdown.invoke(argumentos)
    return filtrar_documento(texto, _nombre(file_path))


@mcp.tool()
def validar_tipo_documental(texto: str) -> dict:
    """Compara texto extraido con referencias locales usando embeddings, sin LLM."""
    from proyecto.agents.document_similarity import evaluate
    return evaluate(texto)


if __name__ == "__main__":
    mcp.run()