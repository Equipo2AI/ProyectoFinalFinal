# Orquestador: un agente de LangChain que decide qué tool MCP usar.

import asyncio
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

from proyecto.agents import (
    data_steward_agent,
    normatividad_rag_agent,
    data_analyst_agent,
    document_intelligence_agent,
    compliance_agent,
)
from proyecto.memory.long_term import LongTermMemory

_RAIZ = Path(__file__).resolve().parents[2]

AGENTES = {
    "data_steward": data_steward_agent,
    "normatividad_rag": normatividad_rag_agent,
    "data_analyst": data_analyst_agent,
    "document_intelligence": document_intelligence_agent,
    "compliance": compliance_agent,
}

SYSTEM_PROMPT = """
Eres el orquestador de un sistema multi-agente bancario.

Tu trabajo es decidir qué herramienta MCP debe atender cada consulta.
Las herramientas disponibles son:

- data_steward: estructura, columnas, faltantes, duplicados y calidad del CSV
  de clientes.
- data_analyst: preguntas cuantitativas que requieren SQL de solo lectura sobre
  la base bancaria (conteos, sumas, promedios, rankings, comparaciones).
- normatividad_rag: políticas, manuales, glosario, reglas y normativa indexada.
- document_intelligence: OCR o descripción de una imagen local. Requiere que la
  consulta incluya una ruta de imagen existente.

Reglas:
- Si la consulta requiere un especialista, llama a la herramienta adecuada.
  Puedes llamar a más de una si la consulta lo necesita.
- Si la consulta es un saludo o algo general que no requiere especialista,
  responde directamente sin llamar herramientas.
- No inventes rutas de imágenes ni datos que no existan.
- Basa tu respuesta únicamente en lo que devuelvan las herramientas. No inventes
  cifras, permisos, requisitos ni fuentes.
- Conserva en tu respuesta final las referencias a archivos o tablas que
  incluyan las herramientas.
- Si no hay una herramienta para el tema (por ejemplo compliance/KYC/AML),
  dilo explícitamente y explica que está fuera del alcance actual.
- Responde en el idioma de la consulta.
"""


def _llamar_mcp(servidor: str, herramienta: str, argumentos: dict) -> str:
    """Ejecuta una tool MCP por stdio y devuelve su texto."""
    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", f"proyecto.mcp_servers.{servidor}"],
        cwd=str(_RAIZ),
    )

    async def ejecutar() -> str:
        async with Client(transporte) as cliente:
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


@tool
def mcp_inspeccionar_dataset() -> str:
    """Consulta por MCP la estructura, columnas y faltantes del CSV de clientes."""
    return _llamar_mcp("data_steward_server", "inspeccionar_dataset", {})


@tool
def mcp_validar_calidad_datos() -> str:
    """Consulta por MCP faltantes, duplicados e IDs repetidos del CSV de clientes."""
    return _llamar_mcp("data_steward_server", "validar_calidad_datos", {})


@tool
def mcp_buscar_normatividad(query: str) -> str:
    """Busca políticas, manuales, glosario y normativa en Pinecone."""
    return _llamar_mcp(
        "normatividad_rag_server",
        "buscar_normatividad",
        {"query": query},
    )


@tool
def mcp_analizar_datos(consulta: str) -> str:
    """Responde preguntas cuantitativas con SQL de solo lectura sobre la base bancaria."""
    return _llamar_mcp(
        "data_analyst_server",
        "analizar_datos",
        {"consulta": consulta},
    )


@tool
def mcp_extraer_texto_imagen(image_path: str) -> str:
    """Extrae el texto visible de una imagen local (factura, recibo, comprobante)."""
    return _llamar_mcp(
        "document_intelligence_server",
        "extraer_texto_imagen",
        {"image_path": image_path},
    )


@tool
def mcp_describir_imagen(image_path: str) -> str:
    """Describe una imagen local y resume sus datos visibles si es un comprobante."""
    return _llamar_mcp(
        "document_intelligence_server",
        "describir_imagen",
        {"image_path": image_path},
    )


HERRAMIENTAS_MCP = [
    mcp_inspeccionar_dataset,
    mcp_validar_calidad_datos,
    mcp_buscar_normatividad,
    mcp_analizar_datos,
    mcp_extraer_texto_imagen,
    mcp_describir_imagen,
]


def listar_agentes() -> dict:
    return {
        nombre: {
            "descripcion": agente.descripcion,
            "modelo": agente.modelo,
            "herramientas": agente.herramientas,
        }
        for nombre, agente in AGENTES.items()
    }


def obtener_agente(nombre: str):
    return AGENTES.get(nombre)


class Orquestador:
    def __init__(
        self,
        modelo: str = "llama3.1:latest",
        memory: LongTermMemory | None = None,
    ):
        self.nombre = "orquestador"
        self.modelo = modelo
        self.herramientas = list(HERRAMIENTAS_MCP)
        self.agentes_invocados: list[str] = []  # trazas de la última consulta
        self._agente = None
        self._mensajes: list = []  # historial de la conversación
        self.memory = memory or LongTermMemory(model=modelo)

    def _get_agente(self):
        if self._agente is None:
            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agente = create_agent(
                model=llm,
                tools=self.herramientas,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agente

    @staticmethod
    def _extraer_texto(mensaje) -> str:
        if isinstance(mensaje.content, str):
            return mensaje.content
        return "".join(
            bloque.get("text", "")
            for bloque in mensaje.content
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )

    def procesar(self, consulta: str) -> str:
        entrada = self.memory.prepare(self._mensajes, consulta)
        resultado = self._get_agente().invoke({"messages": entrada})
        mensajes = resultado["messages"]

        # Qué herramientas MCP llamó el orquestador en este turno.
        nuevos = mensajes[len(entrada):]
        self.agentes_invocados = [
            tc["name"]
            for m in nuevos
            for tc in (getattr(m, "tool_calls", None) or [])
        ]

        self._mensajes = mensajes
        self.memory.update(mensajes)
        return self._extraer_texto(mensajes[-1])

    def reiniciar(self) -> None:
        self._mensajes = []
        self.agentes_invocados = []

    def __repr__(self) -> str:
        return f"<Orquestador | modelo: {self.modelo} | tools MCP: {len(self.herramientas)}>"


orquestador = Orquestador()
