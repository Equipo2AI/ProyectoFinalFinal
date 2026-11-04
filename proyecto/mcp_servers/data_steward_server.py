"""Servidor MCP del Data Steward sobre la base sintética."""

from fastmcp import FastMCP

from proyecto.agents.data_steward_agent import (
    inspeccionar_dataset as inspeccionar_dataset_tool,
    validar_calidad_datos as validar_calidad_datos_tool,
)

mcp = FastMCP("Data Steward MCP")


@mcp.tool()
def inspeccionar_dataset(tabla: str = "") -> str:
    """Consulta tablas, columnas, tipos y faltantes sin revelar registros."""
    return inspeccionar_dataset_tool.invoke({"tabla": tabla})


@mcp.tool()
def validar_calidad_datos(tabla: str = "") -> str:
    """Detecta faltantes, duplicados e identificadores repetidos."""
    return validar_calidad_datos_tool.invoke({"tabla": tabla})


if __name__ == "__main__":
    mcp.run()
