"""Utilidades compartidas por el grafo y el adaptador del orquestador."""

from __future__ import annotations

import asyncio
import os
import sys
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from proyecto.agents.document_intelligence_agent import (
    PATRON_RUTA_DOCUMENTO,
    PATRON_RUTA_IMAGEN,
)

load_dotenv()

_RAIZ = Path(__file__).resolve().parents[2]
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "180"))
MCP_DOCUMENT_TIMEOUT = float(os.getenv("MCP_DOCUMENT_TIMEOUT", "1800"))


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _es_consulta_normativa(consulta: str) -> bool:
    """Ruta de alta confianza para preguntas legales o normativas."""
    texto = _normalizar(consulta)
    return any(
        clave in texto
        for clave in (
            "ley",
            "leyes",
            "normativ",
            "reglamento",
            "regulacion",
            "politica",
            "manual",
            "lineamiento",
            "aviso de privacidad",
            "proteccion de datos",
            "datos personales",
            "privacidad",
            "derechos arco",
            "obligacion",
            "sancion",
            "consentimiento",
            "terminos",
            "condiciones",
        )
    )


def _llamar_mcp(servidor: str, herramienta: str, argumentos: dict) -> str:
    """Ejecuta una tool MCP por stdio y devuelve su texto."""
    from proyecto.security.local_auth import subprocess_env

    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", f"proyecto.mcp_servers.{servidor}"],
        cwd=str(_RAIZ),
        env=subprocess_env(),
    )

    async def ejecutar() -> str:
        timeout = (
            MCP_DOCUMENT_TIMEOUT
            if servidor == "document_intelligence_server"
            else MCP_TIMEOUT
        )
        async with Client(transporte, timeout=timeout) as cliente:
            resultado = await cliente.call_tool(herramienta, argumentos)
            if resultado.is_error:
                raise RuntimeError(
                    f"El servidor MCP {servidor} falló al ejecutar {herramienta}."
                )
            return "\n".join(
                bloque.text
                for bloque in resultado.content
                if bloque.type == "text"
            ).strip()

    try:
        return asyncio.run(ejecutar())
    except Exception as error:  # noqa: BLE001 - se devuelve al agente como texto
        return (
            f"Error al llamar {servidor}.{herramienta}: "
            f"{type(error).__name__}: {error}"
        )


def _ruta_imagen_de_consulta(consulta: str) -> str | None:
    coincidencia = PATRON_RUTA_IMAGEN.search(consulta or "")
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)


def _ruta_documento_de_consulta(consulta: str) -> str | None:
    coincidencia = PATRON_RUTA_DOCUMENTO.search(consulta or "")
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)
