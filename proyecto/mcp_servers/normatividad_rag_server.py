"""Servidor MCP de normatividad.

La herramienta devuelve una respuesta ya sintetizada por el agente RAG, con las
fuentes recuperadas. Así el orquestador recibe una respuesta verificable en vez
de tener que resumir por su cuenta un bloque largo de evidencia.
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


if __name__ == "__main__":
    mcp.run()
