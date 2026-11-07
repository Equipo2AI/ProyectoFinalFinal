"""Servidor MCP demostrativo de compliance."""

import json

from fastmcp import FastMCP

mcp = FastMCP("compliance MCP")


@mcp.tool()
def revisar_compliance_demo(consulta: str) -> str:
    """Revision demostrativa de KYC y AML; no evalua cumplimiento regulatorio real."""
    if not consulta.strip():
        raise ValueError("La consulta no puede estar vacia.")
    return json.dumps(
        {
            "agente": "compliance",
            "estado": "demostracion",
            "consulta": consulta,
            "mensaje": "Revision demostrativa de KYC y AML; no evalua cumplimiento regulatorio real.",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
