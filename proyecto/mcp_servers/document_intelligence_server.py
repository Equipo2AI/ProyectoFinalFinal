"""Servidor MCP de lectura de imágenes (OCR y descripción).

Expone las dos tools del agente `document_intelligence` para que el
orquestador principal pueda llamarlas sin cargar un agente adicional.
"""

from fastmcp import FastMCP

from proyecto.agents.document_intelligence_agent import (
    describe_an_image,
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


if __name__ == "__main__":
    mcp.run()
