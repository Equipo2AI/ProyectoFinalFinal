# Document Intelligence Agent — Document and image reading.
# Domain: OCR, data extraction from invoices/receipts.


from langchain_core.tools import tool

from proyecto.mcp_servers.mcp_client import llamar_herramienta, llamar_herramienta_mcp


async def _analizar_documento_demo_async(consulta: str) -> str:
    return await llamar_herramienta_mcp(
        "proyecto.mcp_servers.document_intelligence_server", "analizar_documento_demo", {"consulta": consulta}
    )


@tool
def analizar_documento_demo_mcp(consulta: str) -> str:
    """Lectura demostrativa de documentos; no realiza OCR ni extrae archivos reales."""
    return llamar_herramienta(
        "proyecto.mcp_servers.document_intelligence_server", "analizar_documento_demo", {"consulta": consulta}
    )


analizar_documento_demo_mcp.coroutine = _analizar_documento_demo_async



class DocumentIntelligenceAgent:
    def __init__(self):
        self.nombre = "document_intelligence"
        self.descripcion = "Lectura de imágenes y documentos (facturas, recibos). Fase futura."
        self.modelo = "llama3.1:latest"
        self.herramientas = [analizar_documento_demo_mcp.name]

    def procesar(self, consulta: str) -> str:
        return analizar_documento_demo_mcp.invoke({"consulta": consulta})

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


document_intelligence_agent = DocumentIntelligenceAgent()
