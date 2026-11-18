
from fastmcp import FastMCP

from proyecto.agents.data_steward_agent import (
    inspect_customer_dataset,
    detect_customer_data_quality_issues,
)

# Creamos el servidor MCP
mcp = FastMCP("Data Steward MCP")


# Primera herramienta
@mcp.tool()
def inspeccionar_dataset() -> str:
    """Consulta la estructura del CSV sin revelar registros personales."""
    return inspect_customer_dataset.invoke({})


# Segunda herramienta
@mcp.tool()
def validar_calidad_datos() -> str:
    """Detecta faltantes, duplicados e identificadores repetidos."""
    return detect_customer_data_quality_issues.invoke({})


# Iniciamos el servidor local
if __name__ == "__main__":
    mcp.run()
