# Orquestador: un agente de LangChain que decide a qué sub-agente delegar.

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama

from agents import (
    data_steward_agent,
    normatividad_rag_agent,
    data_analyst_agent,
    document_intelligence_agent,
    compliance_agent,
)
from memory import LongTermMemory

AGENTES = {
    "data_steward": data_steward_agent,
    "normatividad_rag": normatividad_rag_agent,
    "data_analyst": data_analyst_agent,
    "document_intelligence": document_intelligence_agent,
    "compliance": compliance_agent,
}

SYSTEM_PROMPT = """
Eres el orquestador de un sistema multi-agente bancario.

Tu trabajo es decidir qué agente especialista debe atender cada consulta
y delegarla usando las herramientas disponibles. Cada herramienta es un
agente distinto; lee su descripción para elegir el adecuado.

Reglas:
- Si la consulta requiere un especialista, llama a la herramienta
  correspondiente. Puedes llamar a más de una si la consulta lo necesita.
- Si la consulta es un saludo o algo general que no requiere especialista,
  responde directamente sin llamar herramientas.
- Basa tu respuesta únicamente en lo que devuelvan los agentes. No inventes
  datos, permisos ni requisitos.
- Conserva en tu respuesta final las referencias a archivos fuente que
  incluyan los agentes.
- Responde en el idioma de la consulta.
"""


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


def _crear_herramienta_agente(nombre: str, agente) -> StructuredTool:
    """Envuelve un sub-agente como herramienta que el orquestador puede llamar."""

    def delegar(consulta: str) -> str:
        if not hasattr(agente, "procesar"):
            return f"Error: el agente '{nombre}' no implementa procesar()."
        try:
            return agente.procesar(consulta)
        except Exception as e:
            # Se devuelve como texto para que el orquestador pueda reportarlo
            # en lugar de tumbar toda la conversación.
            return f"Error al ejecutar el agente '{nombre}': {type(e).__name__}: {e}"

    return StructuredTool.from_function(
        func=delegar,
        name=nombre,
        description=agente.descripcion,
    )


class Orquestador:
    def __init__(
        self,
        modelo: str = "gemma4:e4b",
        memory: LongTermMemory | None = None,
    ):
        self.nombre = "orquestador"
        self.modelo = modelo
        self.herramientas = [
            _crear_herramienta_agente(nombre, agente)
            for nombre, agente in AGENTES.items()
        ]
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

        # Qué agentes llamó el orquestador en este turno (para depurar).
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
        return f"<Orquestador | modelo: {self.modelo} | agentes: {len(self.herramientas)}>"


orquestador = Orquestador()