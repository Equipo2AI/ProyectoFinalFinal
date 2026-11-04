"""Servidor MCP demostrativo de document_intelligence."""

import json

from fastmcp import FastMCP

mcp = FastMCP("document_intelligence MCP")


@mcp.tool()
def analizar_documento_demo(consulta: str) -> str:
    """Lectura demostrativa de documentos; no realiza OCR ni extrae archivos reales."""
    if not consulta.strip():
        raise ValueError("La consulta no puede estar vacia.")
    return json.dumps(
        {
            "agente": "document_intelligence",
            "estado": "demostracion",
            "consulta": consulta,
            "mensaje": "Lectura demostrativa de documentos; no realiza OCR ni extrae archivos reales.",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
