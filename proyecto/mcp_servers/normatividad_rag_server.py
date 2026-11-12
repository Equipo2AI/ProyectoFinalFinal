"""Servidor MCP de normatividad.

`buscar_normatividad` conserva el contrato anterior y devuelve evidencia
formateada. `buscar_evidencia` expone la recuperación cruda y `verificar_cita`
comprueba una cita textual contra esa evidencia. Las tres comparten la misma
recuperación cacheada por consulta y no vuelven a consultar el índice.
"""

from fastmcp import FastMCP

from proyecto.agents.normatividad_rag_agent import normatividad_rag_agent

mcp = FastMCP("Normatividad RAG MCP")


@mcp.tool()
def buscar_normatividad(query: str) -> str:
    """Busca y sintetiza normativa, políticas, manuales y avisos de privacidad.

    Args:
        query: Pregunta o tema normativo que se desea consultar.
    """
    return normatividad_rag_agent.procesar(query)


@mcp.tool()
def buscar_evidencia(query: str) -> str:
    """Recupera fragmentos crudos con fuente, página/fila y cita_id, sin sintetizar."""
    herramienta = next(
        t for t in normatividad_rag_agent._tools if t.name == "buscar_evidencia"
    )
    return herramienta.invoke({"query": query})


@mcp.tool()
def verificar_cita(cita: str, query: str = "", cita_id: str = "") -> str:
    """Verifica que una cita textual aparezca en la evidencia recuperada."""
    herramienta = next(
        t for t in normatividad_rag_agent._tools if t.name == "verificar_cita"
    )
    return herramienta.invoke(
        {"cita": cita, "query": query, "cita_id": cita_id}
    )


if __name__ == "__main__":
    mcp.run()
