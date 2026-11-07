# Compliance / Regulatory Agent.
# Domain: KYC, AML, regulatory reporting.


from langchain_core.tools import tool

from proyecto.mcp_servers.mcp_client import llamar_herramienta, llamar_herramienta_mcp


async def _revisar_compliance_demo_async(consulta: str) -> str:
    return await llamar_herramienta_mcp(
        "proyecto.mcp_servers.compliance_server", "revisar_compliance_demo", {"consulta": consulta}
    )


@tool
def revisar_compliance_demo_mcp(consulta: str) -> str:
    """Revision demostrativa de KYC y AML; no evalua cumplimiento regulatorio real."""
    return llamar_herramienta(
        "proyecto.mcp_servers.compliance_server", "revisar_compliance_demo", {"consulta": consulta}
    )


revisar_compliance_demo_mcp.coroutine = _revisar_compliance_demo_async



class ComplianceAgent:
    def __init__(self):
        self.nombre = "compliance"
        self.descripcion = "Reglas regulatorias: KYC, AML, reportes al regulador."
        self.modelo = "llama3.1:latest"
        self.herramientas = [revisar_compliance_demo_mcp.name]

    def procesar(self, consulta: str) -> str:
        return revisar_compliance_demo_mcp.invoke({"consulta": consulta})

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


compliance_agent = ComplianceAgent()
