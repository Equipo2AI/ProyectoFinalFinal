"""Servidor MCP del Data Analyst.

Expone las tools de solo lectura del agente para que puedan ser consumidas
por el orquestador o por cualquier otro cliente MCP.
"""

from fastmcp import FastMCP

from proyecto.agents.data_analyst_agent import data_analyst_agent

mcp = FastMCP("Data Analyst MCP")

_TOOLS = {herramienta.name: herramienta for herramienta in data_analyst_agent._tools}


@mcp.tool()
def analizar_datos(consulta: str) -> str:
    """Responde una pregunta cuantitativa en lenguaje natural usando el agente Data Analyst."""
    return data_analyst_agent.procesar(consulta)


@mcp.tool()
def listar_tablas() -> str:
    """Lista las tablas y vistas disponibles y el motor SQL configurado."""
    return _TOOLS["listar_tablas"].invoke({})


@mcp.tool()
def describir_tabla(tabla: str) -> str:
    """Devuelve columnas, tipos, número de filas y relaciones de una tabla o vista."""
    return _TOOLS["describir_tabla"].invoke({"tabla": tabla})


@mcp.tool()
def valores_columna(tabla: str, columna: str) -> str:
    """Devuelve los valores distintos más frecuentes de una columna categórica."""
    return _TOOLS["valores_columna"].invoke({"tabla": tabla, "columna": columna})


@mcp.tool()
def ejecutar_consulta(sql: str) -> str:
    """Ejecuta una consulta SELECT de solo lectura y devuelve hasta 50 filas."""
    return _TOOLS["ejecutar_consulta"].invoke({"sql": sql})


if __name__ == "__main__":
    mcp.run()
