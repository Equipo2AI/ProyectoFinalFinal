
import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from proyecto.mcp_servers.data_steward_server import mcp


# ==========================================
# PRUEBA 1: CONEXIÓN INTERNA
# ==========================================

async def main():
    async with Client(mcp) as client:

        print("\n=== HERRAMIENTAS MCP ===")

        herramientas = await client.list_tools()

        for herramienta in herramientas:
            print("Herramienta:", herramienta.name)

        print("\n=== PRUEBA 1: INSPECCIONAR DATASET ===")

        resultado1 = await client.call_tool(
            "inspeccionar_dataset", {}
        )

        print(resultado1.content)

        print("\n=== PRUEBA 2: VALIDAR CALIDAD ===")

        resultado2 = await client.call_tool(
            "validar_calidad_datos", {}
        )

        print(resultado2.content)


# ==========================================
# PRUEBA 2: CONEXIÓN MEDIANTE STDIO
# ==========================================

async def probar_stdio():

    print("\n=== PRUEBA MCP CON STDIO ===")

    transporte = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "proyecto.mcp_servers.data_steward_server",
        ],
    )

    async with Client(transporte) as client:

        print("Conexión STDIO establecida")

        herramientas = await client.list_tools()

        for herramienta in herramientas:
            print("Herramienta:", herramienta.name)

        print("\n=== EJECUTAR HERRAMIENTA POR STDIO ===")

        resultado = await client.call_tool(
            "validar_calidad_datos", {}
        )

        print(resultado.content)


# ==========================================
# EJECUTAR TODAS LAS PRUEBAS
# ==========================================

if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(probar_stdio())
