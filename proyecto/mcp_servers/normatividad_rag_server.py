
from fastmcp import FastMCP

from proyecto.agents.normatividad_rag_agent import NormatividadRAGAgent, normatividad_rag_agent

mcp = FastMCP("Normatividad RAG MCP")


@mcp.tool()
def buscar_normatividad(query: str, k: int = 3) -> str:
    """
    Busca información en documentos normativos indexados en Pinecone.

    Args:
        query: Pregunta o tema normativo que se desea consultar.
        k: Numero de resultados; conserva la configuracion del agente cliente.
    """
    agente = normatividad_rag_agent if k == normatividad_rag_agent.k else NormatividadRAGAgent(k=k)
    # La busqueda local se ejecuta aqui; la herramienta cliente no debe llamarse a si misma.
    herramienta = agente._crear_herramienta_busqueda()
    return herramienta.invoke({"query": query})


if __name__ == "__main__":
    mcp.run()
