"""Prueba manual del servidor MCP de document_intelligence.

Uso:
    python tests/probar_document_intelligence_mcp.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from proyecto.mcp_servers.document_intelligence_server import mcp


async def probar_en_proceso() -> None:
    async with Client(mcp) as cliente:
        herramientas = await cliente.list_tools()
        print("Tools:", [herramienta.name for herramienta in herramientas])

        resultado = await cliente.call_tool(
            "extraer_texto_imagen",
            {"image_path": str(RAIZ / "no-existe.png")},
        )
        print("Error esperado:", resultado.content)


async def probar_stdio() -> None:
    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", "proyecto.mcp_servers.document_intelligence_server"],
        cwd=str(RAIZ),
    )
    async with Client(transporte) as cliente:
        herramientas = await cliente.list_tools()
        print("Tools por stdio:", [herramienta.name for herramienta in herramientas])


def main() -> None:
    asyncio.run(probar_en_proceso())
    asyncio.run(probar_stdio())


if __name__ == "__main__":
    main()
