# Orquestador: un agente de LangChain que decide a qué sub-agente delegar.

import json
import re
from contextvars import ContextVar

from langchain.agents import create_agent
from langchain.agents.middleware import after_model, before_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama

from proyecto.agents import (
    data_steward_agent,
    normatividad_rag_agent,
    data_analyst_agent,
    document_intelligence_agent,
    compliance_agent,
)

from proyecto.memory.long_term import LongTermMemory

AGENTES = {
    "data_steward": data_steward_agent,
    "normatividad_rag": normatividad_rag_agent,
    "data_analyst": data_analyst_agent,
    "document_intelligence": document_intelligence_agent,
    "compliance": compliance_agent,
}


_estadisticas_steward = ContextVar("estadisticas_steward", default=None)


class _RegistrarEstadisticasSteward(BaseCallbackHandler):
    """Conserva solo resultados agregados de la herramienta en este turno."""

    def on_tool_end(self, output, **kwargs):
        resultados = _estadisticas_steward.get()
        if resultados is None or getattr(output, "name", None) != "inspeccionar_dataset_mcp":
            return
        try:
            datos = json.loads(output.content)
            columnas = datos["columnas"]
            if (
                datos.get("archivo") == "clientes.csv"
                and type(datos["filas"]) is int and datos["filas"] >= 0
                and isinstance(columnas, list) and columnas
                and all(
                    isinstance(c, dict)
                    and set(c) == {"nombre", "tipo_detectado", "valores_faltantes"}
                    and isinstance(c["nombre"], str)
                    and type(c["valores_faltantes"]) is int
                    and 0 <= c["valores_faltantes"] <= datos["filas"]
                    for c in columnas
                )
            ):
                resultados.append(datos)
        except (ValueError, TypeError, KeyError, AttributeError):
            return


def _respuesta_agregada_verificada(texto: str, datos: dict) -> bool:
    """Acepta un formato agregado acotado y contrasta cada cifra con MCP.

    El resto de las respuestas conserva el flujo habitual. El formato cerrado
    evita aprobar texto arbitrario por el mero hecho de contener números.
    """
    columnas = datos["columnas"]
    faltantes = [c for c in columnas if c["valores_faltantes"]]
    estructura = (
        f"El CSV sintético tiene {datos['filas']} filas y {len(columnas)} columnas."
    )
    if not faltantes:
        return texto.strip() == estructura + " No hay valores faltantes."
    if len({c["valores_faltantes"] for c in faltantes}) != 1:
        return False
    # Solo nombres del esquema, nunca valores de registros ni texto libre.
    nombres = [c["nombre"] for c in faltantes]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", n) for n in nombres):
        return False
    nombres = [json.dumps(n, ensure_ascii=False) for n in nombres]
    lista = " y ".join(nombres) if len(nombres) <= 2 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
    cantidad = faltantes[0]["valores_faltantes"]
    unidad = "valor faltante" if cantidad == 1 else "valores faltantes"
    esperado = (
        estructura + f" Hay valores faltantes en {len(faltantes)} columnas: "
        + lista + f", con {cantidad} {unidad} cada una."
    )
    return texto.strip() == esperado


@before_model(can_jump_to=["end"])
def _conservar_agregado_steward(state, runtime):
    mensajes = state["messages"]
    inicio = next(
        (i for i in range(len(mensajes) - 1, -1, -1) if isinstance(mensajes[i], HumanMessage)),
        0,
    )
    nuevos = mensajes[inicio + 1:]
    llamadas = [tc for m in nuevos for tc in (getattr(m, "tool_calls", None) or [])]
    resultados = _estadisticas_steward.get()
    if len(llamadas) != 1 or llamadas[0]["name"] != "data_steward" or not resultados:
        return None
    ultimo = mensajes[-1]
    if (
        isinstance(ultimo, ToolMessage)
        and ultimo.tool_call_id == llamadas[0]["id"]
        and getattr(ultimo, "status", "success") != "error"
        and isinstance(ultimo.content, str)
        and len(resultados) == 1
        and _respuesta_agregada_verificada(ultimo.content, resultados[0])
    ):
        return {"messages": [AIMessage(content=ultimo.content)], "jump_to": "end"}
    return None

SYSTEM_PROMPT = """
Eres el orquestador de un sistema multi-agente bancario.

Tu trabajo es decidir qué agente especialista debe atender cada consulta
y delegarla usando las herramientas disponibles. Cada herramienta es un
agente distinto; lee su descripción para elegir el adecuado.

Reglas:
- Estructura o calidad del CSV (columnas, faltantes, duplicados e identificadores
  repetidos): delega a data_steward. Un CSV no es una tabla de la base SQL.
- Analisis cuantitativo de la base bancaria, consultas SQL, tablas o vistas:
  delega a data_analyst. No le envies revisiones de calidad del CSV.
- Al delegar, conserva la pregunta del usuario. No la conviertas en SQL ni
  inventes tablas, columnas o herramientas; el especialista consulta su esquema.
- Si la consulta requiere un especialista, llama a la herramienta
  correspondiente. Puedes llamar a más de una si la consulta lo necesita.
- Si la consulta es un saludo o algo general que no requiere especialista,
  responde directamente sin llamar herramientas.
- Basa tu respuesta únicamente en lo que devuelvan los agentes. No inventes
  datos, permisos ni requisitos.
- Conserva en tu respuesta final las referencias a archivos fuente que
  incluyan los agentes.
- Tras recibir el resultado del agente, comunica su contenido fielmente.
  No escribas llamadas a herramientas como texto ni repitas delegaciones ya resueltas.
- Si el resultado de un agente es un JSON con estado "demostracion", devuelve
  literalmente ese JSON como respuesta final, sin texto adicional. Describe una
  prueba de infraestructura MCP: no confirma analisis, OCR ni cumplimiento real.
- Solo cita archivos fuente que aparezcan explicitamente en el resultado del
  agente. No deduzcas nombres de archivos o configuraciones a partir de su nombre.
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


@after_model(can_jump_to=["end"])
def _evitar_delegacion_repetida(state, runtime):
    """Finaliza una repeticion exacta con resultados ya recibidos en este turno.

    No bloquea consultas nuevas, agentes adicionales ni reintentos de errores.
    """
    mensajes = state["messages"]
    ultimo = mensajes[-1]
    solicitudes = getattr(ultimo, "tool_calls", None) or []
    if not solicitudes:
        return None

    def clave(llamada):
        return llamada["name"], json.dumps(llamada["args"], sort_keys=True, ensure_ascii=False)

    inicio = next(
        (i for i in range(len(mensajes) - 1, -1, -1) if isinstance(mensajes[i], HumanMessage)),
        0,
    )
    pendientes = {}
    completadas = {}
    for mensaje in mensajes[inicio:-1]:
        for llamada in getattr(mensaje, "tool_calls", None) or []:
            pendientes[llamada["id"]] = clave(llamada)
        if (
            isinstance(mensaje, ToolMessage)
            and mensaje.tool_call_id in pendientes
            and getattr(mensaje, "status", "success") != "error"
            and isinstance(mensaje.content, str)
            and not mensaje.content.startswith("Error al ejecutar el agente")
        ):
            completadas[pendientes[mensaje.tool_call_id]] = mensaje.content

    claves = list(dict.fromkeys(clave(llamada) for llamada in solicitudes))
    if not all(clave_actual in completadas for clave_actual in claves):
        return None
    contenido = "\n\n".join(completadas[clave_actual] for clave_actual in claves)
    return {
        "messages": [AIMessage(id=ultimo.id, content=contenido)],
        "jump_to": "end",
    }


class Orquestador:
    def __init__(
        self,
        modelo: str = "llama3.1:latest",
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
                middleware=[_conservar_agregado_steward, _evitar_delegacion_repetida],
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
        token = _estadisticas_steward.set([])
        try:
            resultado = self._get_agente().invoke(
                {"messages": entrada},
                config={"callbacks": [_RegistrarEstadisticasSteward()]},
            )
        finally:
            _estadisticas_steward.reset(token)
        mensajes = resultado["messages"]

        # Qué agentes llamó el orquestador en este turno (para depurar).
        nuevos = mensajes[len(entrada):]
        self.agentes_invocados = [
            tc["name"]
            for m in nuevos
            for tc in (getattr(m, "tool_calls", None) or [])
        ]

        # Una demostracion MCP no necesita reinterpretacion del resultado.
        # Conservamos el JSON real tambien en el historial y la memoria.
        if len(set(self.agentes_invocados)) == 1:
            for mensaje in reversed(nuevos):
                if (
                    getattr(mensaje, "type", None) == "tool"
                    and getattr(mensaje, "name", None) == self.agentes_invocados[0]
                    and isinstance(mensaje.content, str)
                ):
                    try:
                        datos = json.loads(mensaje.content)
                    except (ValueError, TypeError):
                        break
                    if isinstance(datos, dict) and datos.get("estado") == "demostracion":
                        mensajes[-1].content = mensaje.content
                    break

        self._mensajes = mensajes
        self.memory.update(mensajes)
        return self._extraer_texto(mensajes[-1])

    def reiniciar(self) -> None:
        self._mensajes = []
        self.agentes_invocados = []

    def __repr__(self) -> str:
        return f"<Orquestador | modelo: {self.modelo} | agentes: {len(self.herramientas)}>"


orquestador = Orquestador()
