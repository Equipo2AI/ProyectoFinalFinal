"""Servidor MCP demostrativo de data_analyst."""

import json

from fastmcp import FastMCP

mcp = FastMCP("data_analyst MCP")


@mcp.tool()
def analizar_datos_demo(consulta: str) -> str:
    """Analisis cuantitativo demostrativo; no ejecuta SQL ni calcula metricas reales."""
    if not consulta.strip():
        raise ValueError("La consulta no puede estar vacia.")
    return json.dumps(
        {
            "agente": "data_analyst",
            "estado": "demostracion",
            "consulta": consulta,
            "mensaje": "Analisis cuantitativo demostrativo; no ejecuta SQL ni calcula metricas reales.",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
