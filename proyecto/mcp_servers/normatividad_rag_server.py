
from fastmcp import FastMCP

from proyecto.agents.normatividad_rag_agent import normatividad_rag_agent

mcp = FastMCP("Normatividad RAG MCP")


@mcp.tool()
def buscar_normatividad(query: str) -> str:
    """
    Busca información en documentos normativos indexados en Pinecone.

    Args:
        query: Pregunta o tema normativo que se desea consultar.
    """
    herramienta = normatividad_rag_agent.herramientas[0]
    return herramienta.invoke({"query": query})


if __name__ == "__main__":
    mcp.run()
