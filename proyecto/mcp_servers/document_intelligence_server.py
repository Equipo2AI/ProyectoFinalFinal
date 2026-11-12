"""Servidor MCP de imágenes y documentos.

Expone OCR, descripción de imágenes y conversión de documentos a Markdown
para que el orquestador principal pueda usar el agente sin cargarlo
directamente.
"""

from fastmcp import FastMCP

from proyecto.agents.document_intelligence_agent import (
    describe_an_image,
    document_to_markdown,
    ocr_image,
)

mcp = FastMCP("Document Intelligence MCP")


@mcp.tool()
def extraer_texto_imagen(image_path: str) -> str:
    """Extrae el texto visible de una imagen local (factura, recibo, comprobante)."""
    return ocr_image.invoke({"image_path": image_path})


@mcp.tool()
def describir_imagen(image_path: str) -> str:
    """Describe una imagen local y resume sus datos visibles si es un comprobante."""
    return describe_an_image.invoke({"image_path": image_path})


@mcp.tool()
def convertir_documento_markdown(
    file_path: str,
    modo: str = "auto",
    paginas: str = "",
) -> str:
    """Convierte un documento local a Markdown.

    En PDF extrae el texto nativo y aplica OCR local a las páginas escaneadas.
    """
    argumentos: dict[str, str] = {"file_path": file_path, "modo": modo or "auto"}
    if (paginas or "").strip():
        argumentos["paginas"] = paginas
    return document_to_markdown.invoke(argumentos)


if __name__ == "__main__":
    mcp.run()
