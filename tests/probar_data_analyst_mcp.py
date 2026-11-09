"""Prueba manual del servidor MCP de data_analyst.

Uso:
    python tests/probar_data_analyst_mcp.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from proyecto.mcp_servers.data_analyst_server import mcp


async def probar_en_proceso() -> None:
    async with Client(mcp) as cliente:
        herramientas = await cliente.list_tools()
        print("Tools:", [herramienta.name for herramienta in herramientas])

        resultado = await cliente.call_tool(
            "ejecutar_consulta",
            {"sql": "DELETE FROM dim_cliente"},
        )
        print("Guard de escritura:", resultado.content)


async def probar_stdio() -> None:
    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", "proyecto.mcp_servers.data_analyst_server"],
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
