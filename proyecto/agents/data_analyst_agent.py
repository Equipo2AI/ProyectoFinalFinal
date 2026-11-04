# Data Analyst Agent — Quantitative analysis and queries.
# Domain: SQL queries, metrics, transactional data visualization.


from langchain_core.tools import tool

from proyecto.mcp_servers.mcp_client import llamar_herramienta, llamar_herramienta_mcp


async def _analizar_datos_demo_async(consulta: str) -> str:
    return await llamar_herramienta_mcp(
        "proyecto.mcp_servers.data_analyst_server", "analizar_datos_demo", {"consulta": consulta}
    )


@tool
def analizar_datos_demo_mcp(consulta: str) -> str:
    """Analisis cuantitativo demostrativo; no ejecuta SQL ni calcula metricas reales."""
    return llamar_herramienta(
        "proyecto.mcp_servers.data_analyst_server", "analizar_datos_demo", {"consulta": consulta}
    )


analizar_datos_demo_mcp.coroutine = _analizar_datos_demo_async



class DataAnalystAgent:
    def __init__(self):
        self.nombre = "data_analyst"
        self.descripcion = "Análisis cuantitativo sobre datos transaccionales bancarios."
        self.modelo = "llama3.1:latest"
        self.herramientas = [analizar_datos_demo_mcp.name]

    def procesar(self, consulta: str) -> str:
        return analizar_datos_demo_mcp.invoke({"consulta": consulta})

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_analyst_agent = DataAnalystAgent()
