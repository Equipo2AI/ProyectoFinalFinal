"""Cliente MCP STDIO reutilizable para los cinco servidores independientes."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

logger = logging.getLogger(__name__)


def crear_transporte(modulo: str, *, env: dict[str, str] | None = None) -> StdioTransport:
    """Usa el Python activo y una ruta estable, independiente del directorio actual."""
    from proyecto.security.local_auth import subprocess_env
    return StdioTransport(
        command=sys.executable,
        args=["-m", modulo],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**subprocess_env(), **(env or {})},
    )


async def llamar_herramienta_mcp(
    modulo: str, nombre: str, argumentos: dict[str, Any] | None = None,
    *, env: dict[str, str] | None = None,
) -> str:
    """Ejecuta una herramienta real y cierra la sesion y el subproceso al terminar."""
    transporte = crear_transporte(modulo, env=env) if env is not None else crear_transporte(modulo)
    timeout = float(os.getenv("MCP_TIMEOUT", "180"))
    async with Client(transporte, timeout=timeout) as cliente:
        resultado = await cliente.call_tool(nombre, argumentos or {})
        if resultado.is_error:
            raise RuntimeError(f"Error al ejecutar la herramienta MCP: {nombre}")
        texto = "\n".join(
            bloque.text for bloque in resultado.content if bloque.type == "text"
        )
        # Se registra solo el destino completado; nunca consultas ni datos personales.
        logger.info(
            "MCP STDIO completado: %s -> %s", modulo, nombre,
            extra={"mcp_modulo": modulo, "mcp_herramienta": nombre},
        )
        return texto


def llamar_herramienta(
    modulo: str, nombre: str, argumentos: dict[str, Any] | None = None,
    *, env: dict[str, str] | None = None,
) -> str:
    """Adaptador para las interfaces sincronas procesar del orquestador."""
    return asyncio.run(llamar_herramienta_mcp(modulo, nombre, argumentos, env=env))
