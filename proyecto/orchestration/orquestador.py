# Orquestador: un agente de LangChain que decide qué tool MCP usar.

import asyncio
import json
import logging
import os
import sys
import unicodedata
from contextvars import ContextVar
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

from proyecto.agents import (
    data_steward_agent,
    normatividad_rag_agent,
    data_analyst_agent,
    document_intelligence_agent,
    compliance_agent,
)
from proyecto.agents.document_intelligence_agent import PATRON_RUTA_IMAGEN
from proyecto.config.models import modelo_de
from proyecto.memory.long_term import LongTermMemory

_RAIZ = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "180"))

AGENTES = {
    "data_steward": data_steward_agent,
    "normatividad_rag": normatividad_rag_agent,
    "data_analyst": data_analyst_agent,
    "document_intelligence": document_intelligence_agent,
    "compliance": compliance_agent,
}

SYSTEM_PROMPT = """
Eres el orquestador de un sistema multi-agente bancario.

Tu trabajo es decidir qué herramienta MCP debe atender cada consulta. No respondas preguntas fuera de lo señalado o del ámbito bancario/financiero.
Las herramientas disponibles son:

- data_steward: estructura, tablas, columnas, faltantes, duplicados y calidad de
  la base sintética bancaria. No devuelve registros personales.
- data_analyst: preguntas cuantitativas que requieren SQL de solo lectura sobre
  la misma base sintética (conteos, sumas, promedios, rankings, comparaciones).
- normatividad_rag: políticas, manuales, glosario, reglas y normativa indexada.
- document_intelligence: OCR o descripción de una imagen local. Requiere que la
  consulta incluya una ruta de imagen existente.

Reglas:
_ A cualquier tema ajeno a temas bancarios o financieros, no contestar e indicarle al usuario que
ese no es tu propósito.
- Si la consulta requiere un especialista, llama a la herramienta adecuada.
  Puedes llamar a más de una si la consulta lo necesita.
- Para preguntas sobre leyes, protección de datos, privacidad, avisos de
  privacidad, políticas, manuales o normativa, SIEMPRE llama primero a
  mcp_buscar_normatividad. No respondas de memoria ni inventes artículos.
- Después de una búsqueda normativa, responde con la evidencia recuperada y
  conserva las referencias FUENTE (archivo y página) en tu respuesta.
- La salida de mcp_buscar_normatividad es la única fuente válida para una
  pregunta normativa. Si la evidencia contiene el artículo o la frase que
  responde, cítala textualmente. No digas "la información no incluye" ni
  "podemos inferir" cuando el dato sí aparece en la evidencia. No agregues
  derechos, obligaciones ni artículos que no estén en la evidencia.
- Usa data_steward para estructura y calidad (tablas, columnas, faltantes,
  duplicados); usa data_analyst para cifras y agregados. No uses data_analyst
  para preguntas de metadatos ni data_steward para calcular métricas.
- En la salida de data_steward, `catalogo_tablas` es solo el inventario de
  tablas disponibles; no son relaciones. Las relaciones reales están en
  `relaciones` y son foreign keys declaradas. Para tablas `raw_*` con hallazgos
  esperados, usa `mcp_evaluar_calidad` y compara esperado contra encontrado.
- mcp_analizar_datos recibe la pregunta en lenguaje natural, no una consulta
  SQL. Pásale el texto del usuario y deja que el Data Analyst escriba el SQL.
- Si el usuario pide una gráfica o visualización, después de mcp_analizar_datos
  llama a mcp_graficar_consulta con el SQL exacto que devolvió el analista.
  No inventes el SQL ni grafiques identificadores o datos personales.
- Si el usuario responde con una aclaración o confirmación (por ejemplo "sí",
  "por género" o "solo 2026") a una pregunta anterior de Data Analyst, vuelve
  a llamar mcp_analizar_datos con una consulta autocontenida que incluya la
  pregunta original y la aclaración.
- Si la consulta incluye una imagen pero no dice si hay que extraer texto o
  describirla, elige la opción más útil según la intención. Si sigue siendo
  ambigua, pregunta al usuario antes de llamar la herramienta.
- Si la consulta es un saludo o algo general que no requiere especialista,
  responde directamente sin llamar herramientas.
- No inventes rutas de imágenes ni datos que no existan.
- Basa tu respuesta únicamente en lo que devuelvan las herramientas. No inventes
  cifras, permisos, requisitos ni fuentes.
- Eres un asistente documental, no un abogado: puedes resumir y citar normativa,
  avisos de privacidad y políticas internas. No rechaces una pregunta legítima
  sobre una ley o un aviso de privacidad; respóndela con la evidencia recuperada
  y aclara que no sustituye asesoría legal personalizada.
- Conserva en tu respuesta final las referencias a archivos o tablas que
  incluyan las herramientas.
- Si el contexto incluye una imagen o documento ya procesado, úsalo para
  responder preguntas sobre su contenido (totales, fechas, folios, partes,
  condiciones, etc.). Esas preguntas no son consultas a la base bancaria:
  no uses data_analyst para responderlas.
- Si el contexto documental ya contiene el texto o la descripción, responde
  directamente con esa evidencia. No vuelvas a llamar a las herramientas de
  imagen para preguntas de seguimiento; la imagen ya fue procesada.
- Cuando uses mcp_extraer_texto_imagen, reproduce el texto extraído completo
  en un bloque de código, sin resumirlo, salvo que el usuario pida
  explícitamente un resumen o una descripción.
- No atribuyas una cifra, conclusión o fuente a una herramienta que no la
  devolvió. Si usaste el contexto de un documento, dilo con claridad.
- No incluyas rutas locales, enlaces Markdown ni imágenes en la respuesta;
  menciona el nombre del documento como texto si necesitas identificarlo.
- Si una herramienta devuelve un error, informa el error y no lo presentes
  como si fuera un resultado válido.
- Si no hay una herramienta para el tema (por ejemplo compliance/KYC/AML),
  dilo explícitamente y explica que está fuera del alcance actual.
- Responde en el idioma de la consulta.
"""

_MARCADOR_CONTEXTO = "[CONTEXTO_DOCUMENTOS]"
_CONTEXTO_ANALISTA: ContextVar[str] = ContextVar("contexto_analista", default="")
_PERFIL_ANALISTA: ContextVar[dict] = ContextVar("perfil_analista", default={})


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _ruta_imagen_de_consulta(consulta: str) -> str | None:
    coincidencia = PATRON_RUTA_IMAGEN.search(consulta or "")
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)


def _es_consulta_normativa(consulta: str) -> bool:
    """Ruta de alta confianza para preguntas legales o normativas.

    El modelo orquestador puede reformular la consulta y perder artículos
    relevantes. Para la demo, estas preguntas se envían directo al servidor de
    normatividad con el texto original y se devuelve su respuesta con fuentes.
    """
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
            "términos",
            "condiciones"
        )
    )


def _llamar_mcp(servidor: str, herramienta: str, argumentos: dict) -> str:
    """Ejecuta una tool MCP por stdio y devuelve su texto."""
    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", f"proyecto.mcp_servers.{servidor}"],
        cwd=str(_RAIZ),
    )

    async def ejecutar() -> str:
        async with Client(transporte, timeout=MCP_TIMEOUT) as cliente:
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
def mcp_inspeccionar_dataset(tabla: str = "") -> str:
    """Consulta por MCP tablas, columnas, tipos y faltantes de la base sintética.

    Args:
        tabla: Tabla o vista a inspeccionar. Vacío usa raw_clientes.
    """
    return _llamar_mcp(
        "data_steward_server",
        "inspeccionar_dataset",
        {"tabla": tabla},
    )


@tool
def mcp_validar_calidad_datos(tabla: str = "") -> str:
    """Consulta por MCP faltantes, duplicados e IDs repetidos de la base sintética.

    Args:
        tabla: Tabla o vista a validar. Vacío usa raw_clientes.
    """
    return _llamar_mcp(
        "data_steward_server",
        "validar_calidad_datos",
        {"tabla": tabla},
    )


@tool
def mcp_evaluar_calidad(tabla: str = "") -> str:
    """Compara una tabla contra los hallazgos esperados de calidad.

    Args:
        tabla: Tabla o vista a evaluar. Vacío usa raw_clientes.
    """
    return _llamar_mcp(
        "data_steward_server",
        "evaluar_calidad",
        {"tabla": tabla},
    )


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
        {
            "consulta": consulta,
            "contexto": _CONTEXTO_ANALISTA.get(),
            "perfil": _PERFIL_ANALISTA.get() or None,
        },
    )


@tool
def mcp_graficar_consulta(
    sql: str,
    tipo: str = "auto",
    eje_x: str = "",
    eje_y: str = "",
    titulo: str = "",
    orden: str = "ninguno",
) -> str:
    """Genera una gráfica PNG a partir de una consulta SQL agregada.

    Úsala solo cuando el usuario pida una gráfica o visualización. Reutiliza el
    SQL que ya devolvió mcp_analizar_datos y respeta el perfil de la sesión.
    """
    return _llamar_mcp(
        "data_analyst_server",
        "graficar_consulta",
        {
            "sql": sql,
            "tipo": tipo,
            "eje_x": eje_x,
            "eje_y": eje_y,
            "titulo": titulo,
            "orden": orden,
            "perfil": _PERFIL_ANALISTA.get() or None,
        },
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
    mcp_evaluar_calidad,
    mcp_buscar_normatividad,
    mcp_analizar_datos,
    mcp_graficar_consulta,
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
        modelo: str | None = None,
        memory: LongTermMemory | None = None,
    ):
        self.nombre = "orquestador"
        self.modelo = modelo or modelo_de("orchestrator")
        self.herramientas = list(HERRAMIENTAS_MCP)
        self.agentes_invocados: list[str] = []  # trazas de la última consulta
        self.ultimas_herramientas: list[dict] = []  # nombre, args, salida, estado
        self.ultimo_estado_grafo: dict = {}  # resultados estructurados del turno
        self._agente = None
        self._mensajes: list = []  # historial de la conversación
        self._documentos: list[dict] = []  # OCR/descripción usados como contexto
        self._imagen_pendiente: str | None = None
        self._ultimo_agente: str | None = None
        self.modo = os.getenv("ORCHESTRATION_MODE", "graph").strip().lower()
        if self.modo not in {"graph", "hub"}:
            self.modo = "graph"
        self.modo_efectivo = self.modo
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

    def registrar_imagen(self, ruta: str, pregunta: str) -> None:
        """Deja una imagen pendiente en el historial para la siguiente consulta."""
        self._imagen_pendiente = str(Path(ruta).expanduser().resolve())
        self._mensajes = [
            *self._mensajes,
            HumanMessage(content=f"Adjunté una imagen en {self._imagen_pendiente}."),
            AIMessage(content=pregunta),
        ]
        self.agentes_invocados = []
        self.ultimas_herramientas = []
        self.memory.update(self._mensajes)

    @property
    def imagen_pendiente(self) -> str | None:
        return self._imagen_pendiente

    def limpiar_imagen_pendiente(self) -> None:
        self._imagen_pendiente = None

    def marcar_imagen_pendiente(self, ruta: str) -> None:
        self._imagen_pendiente = str(Path(ruta).expanduser().resolve())

    def registrar_documento(
        self,
        ruta: str,
        operacion: str,
        texto: str,
        nombre: str | None = None,
    ) -> None:
        """Guarda el texto de una imagen para responder consultas posteriores."""
        texto = (texto or "").strip()
        if not texto or texto.startswith("No se pudo procesar la imagen:"):
            return

        ruta_resuelta = str(Path(ruta).expanduser().resolve())
        self._documentos = [
            documento
            for documento in self._documentos
            if documento.get("ruta") != ruta_resuelta
        ]
        self._documentos.append(
            {
                "ruta": ruta_resuelta,
                "nombre": nombre or Path(ruta_resuelta).name,
                "operacion": operacion,
                "texto": texto,
            }
        )
        self._documentos = self._documentos[-5:]
        self._ultimo_agente = "document_intelligence"
        if self._imagen_pendiente == ruta_resuelta:
            self._imagen_pendiente = None

    def _contexto_documentos(self) -> str:
        partes: list[str] = []
        for documento in self._documentos[-3:]:
            texto = str(documento.get("texto") or "")
            if len(texto) > 6000:
                texto = texto[:6000].rstrip() + "\n[…texto truncado…]"
            partes.append(
                f"Documento: {documento.get('nombre')} "
                f"({documento.get('operacion')})\n"
                f"Ruta original: {documento.get('ruta')}\n"
                f"Texto ya extraído:\n{texto}"
            )

        if self._imagen_pendiente:
            partes.append(
                "Imagen pendiente sin procesar: "
                f"{self._imagen_pendiente}. Si el usuario pide leerla o "
                "describirla, usa la herramienta MCP de document_intelligence."
            )

        if not partes:
            return ""
        return (
            f"{_MARCADOR_CONTEXTO}\n"
            "Contexto documental de esta conversación. Úsalo como evidencia "
            "para preguntas de seguimiento; no lo envíes a data_analyst ni "
            "vuelvas a procesar la imagen salvo que el usuario lo pida.\n\n"
            + "\n\n".join(partes)
        )

    def _contexto_analista(self) -> str:
        """Resumen breve del hilo para resolver aclaraciones de SQL."""
        partes: list[str] = []
        for mensaje in self._mensajes[-6:]:
            tipo = getattr(mensaje, "type", "")
            if tipo not in {"human", "ai"}:
                continue
            texto = self._extraer_texto(mensaje).strip()
            if not texto:
                continue
            rol = "Usuario" if tipo == "human" else "Asistente"
            partes.append(f"{rol}: {texto[:800]}")
        return "\n".join(partes)

    def _resultados_data_steward(self) -> list[dict]:
        """Recupera las salidas estructuradas del Data Steward para verificarlas."""
        nombres = {
            "mcp_inspeccionar_dataset",
            "mcp_validar_calidad_datos",
        }
        if not self.ultimas_herramientas:
            return []

        resultados: list[dict] = []
        for registro in self.ultimas_herramientas:
            nombre = registro.get("name")
            if nombre not in nombres:
                return []
            salida = str(registro.get("salida") or "").strip()
            if not salida or registro.get("estado") == "error":
                return []
            try:
                datos = json.loads(salida)
            except (TypeError, ValueError):
                return []
            herramienta = (
                "inspeccionar_dataset"
                if nombre == "mcp_inspeccionar_dataset"
                else "validar_calidad_datos"
            )
            resultados.append({"herramienta": herramienta, "datos": datos})
        return resultados

    def _guardar_respuesta_verificada(self, respuesta: str, mensajes: list) -> str:
        mensajes = [
            mensaje
            for mensaje in mensajes
            if getattr(mensaje, "type", "") != "system"
        ]
        if mensajes and getattr(mensajes[-1], "type", "") == "ai":
            mensajes = [*mensajes[:-1], AIMessage(content=respuesta)]
        else:
            mensajes = [*mensajes, AIMessage(content=respuesta)]
        self._mensajes = mensajes
        self.memory.update(self._mensajes)
        return respuesta

    def _registrar_herramientas(self, nuevos: list) -> None:
        """Empareja tool calls con sus resultados para una traza verificable."""
        registros: list[dict] = []
        por_id: dict[str, dict] = {}

        for mensaje in nuevos:
            for llamada in getattr(mensaje, "tool_calls", None) or []:
                registro = {
                    "name": llamada.get("name"),
                    "args": llamada.get("args") or {},
                    "salida": "",
                    "estado": "ok",
                }
                registros.append(registro)
                if llamada.get("id"):
                    por_id[llamada["id"]] = registro

            if getattr(mensaje, "type", "") == "tool":
                registro = por_id.get(getattr(mensaje, "tool_call_id", None))
                if registro is None and registros:
                    registro = registros[-1]
                if registro is None:
                    continue
                salida = self._extraer_texto(mensaje).strip()
                registro["salida"] = salida
                if getattr(mensaje, "status", None) == "error" or salida.startswith(
                    ("Error al llamar", "No se pudo procesar la imagen:")
                ):
                    registro["estado"] = "error"

        self.ultimas_herramientas = registros
        self.agentes_invocados = [
            registro["name"]
            for registro in registros
            if registro.get("name")
        ]

    def _responder_normatividad(self, consulta: str) -> str:
        """Ruta directa al servidor de normatividad para consultas legales."""
        salida = _llamar_mcp(
            "normatividad_rag_server",
            "buscar_normatividad",
            {"query": consulta},
        )
        estado = (
            "error"
            if salida.startswith("Error al llamar ")
            else "ok"
        )
        self.ultimas_herramientas = [
            {
                "name": "mcp_buscar_normatividad",
                "args": {"query": consulta},
                "salida": salida,
                "estado": estado,
            }
        ]
        self.agentes_invocados = (
            ["mcp_buscar_normatividad"] if estado == "ok" else []
        )
        self._mensajes = [
            *self._mensajes,
            HumanMessage(content=consulta),
            AIMessage(content=salida),
        ]
        self._ultimo_agente = "normatividad_rag"
        self.memory.update(self._mensajes)
        return salida

    def procesar(self, consulta: str, perfil: dict | None = None) -> str:
        """Atiende una consulta con el modo de orquestación configurado.

        ``ORCHESTRATION_MODE=graph`` usa el grafo explícito de LangGraph.
        ``ORCHESTRATION_MODE=hub`` conserva el orquestador anterior como
        respaldo. Si el grafo falla, se intenta el hub para no dejar la UI sin
        respuesta.

        ``perfil`` es el contexto de sesión autenticada (rol y cliente_id).
        """
        self.ultimo_estado_grafo = {}
        if self.modo == "hub":
            return self._procesar_hub(consulta, perfil=perfil)

        try:
            return self._procesar_grafo(consulta, perfil=perfil)
        except Exception as error:  # noqa: BLE001 - fallback deliberado
            logger.exception("Falló el grafo; se usa el orquestador hub: %s", error)
            self.modo_efectivo = "hub (fallback)"
            return self._procesar_hub(consulta, perfil=perfil)

    def _procesar_grafo(
        self, consulta: str, perfil: dict | None = None
    ) -> str:
        from proyecto.orchestration.grafo import ejecutar_grafo

        historial = self.memory.prepare(self._mensajes, consulta)
        estado = ejecutar_grafo(
            consulta,
            historial=historial,
            contexto_documentos=self._contexto_documentos(),
            contexto_analista=self._contexto_analista(),
            modelo=self.modelo,
            imagen_pendiente=self._imagen_pendiente,
            ultimo_agente=self._ultimo_agente,
            perfil=perfil,
        )

        respuesta = str(estado.get("respuesta") or "").strip()
        if not respuesta:
            raise RuntimeError("El grafo terminó sin respuesta.")

        self.ultimo_estado_grafo = estado
        trazas = list(estado.get("trazas") or [])
        self.ultimas_herramientas = trazas
        self.agentes_invocados = [
            str(traza.get("name"))
            for traza in trazas
            if traza.get("name")
        ]

        # Si el grafo procesó una imagen, guardamos el resultado para las
        # preguntas de seguimiento y limpiamos la imagen pendiente.
        ruta_documento = estado.get("imagen_pendiente")
        for traza in trazas:
            nombre = str(traza.get("name") or "")
            if (
                nombre not in {"mcp_extraer_texto_imagen", "mcp_describir_imagen"}
                or traza.get("estado") == "error"
            ):
                continue
            ruta = ruta_documento or _ruta_imagen_de_consulta(consulta)
            if not ruta:
                continue
            operacion = (
                "descripcion"
                if nombre == "mcp_describir_imagen"
                else "ocr"
            )
            self.registrar_documento(
                ruta,
                operacion,
                str(traza.get("salida") or ""),
                Path(ruta).name,
            )

        plan = list(estado.get("plan") or [])
        if plan and plan[-1] != "directo":
            self._ultimo_agente = plan[-1]
        self._mensajes = [
            *self._mensajes,
            HumanMessage(content=consulta),
            AIMessage(content=respuesta),
        ]
        self.memory.update(self._mensajes)
        self.modo_efectivo = "grafo"
        return respuesta

    def _procesar_hub(
        self, consulta: str, perfil: dict | None = None
    ) -> str:
        self.modo_efectivo = "hub"
        if _es_consulta_normativa(consulta):
            return self._responder_normatividad(consulta)

        entrada = self.memory.prepare(self._mensajes, consulta)
        contexto = self._contexto_documentos()
        if contexto:
            entrada = [{"role": "system", "content": contexto}, *entrada]

        token_contexto = _CONTEXTO_ANALISTA.set(self._contexto_analista())
        token_perfil = _PERFIL_ANALISTA.set(dict(perfil or {}))
        try:
            resultado = self._get_agente().invoke(
                {"messages": entrada},
                config={
                    "run_name": "orquestador_mcp",
                    "tags": ["orquestador", "mcp", "ui"],
                    "metadata": {
                        "modelo": self.modelo,
                        "herramientas": [t.name for t in self.herramientas],
                    },
                },
            )
        finally:
            _CONTEXTO_ANALISTA.reset(token_contexto)
            _PERFIL_ANALISTA.reset(token_perfil)
        mensajes = resultado["messages"]

        # Qué herramientas MCP llamó el orquestador en este turno.
        nuevos = mensajes[len(entrada):]
        self._registrar_herramientas(nuevos)

        # El Data Steward devuelve JSON de metadatos. Lo formateamos sin pasar
        # por el LLM para que no invente columnas, conteos ni relaciones.
        resultados_steward = self._resultados_data_steward()
        if resultados_steward:
            respuesta = data_steward_agent.formatear_resultados_verificados(
                resultados_steward
            )
            return self._guardar_respuesta_verificada(respuesta, mensajes)

        # Normatividad y Data Analyst ya devuelven una respuesta verificada.
        # Si fueron la única herramienta, se usa tal cual: evita que el modelo
        # orquestador reescriba la evidencia, el SQL o las cifras.
        if (
            len(self.ultimas_herramientas) == 1
            and self.ultimas_herramientas[0].get("name")
            in {"mcp_buscar_normatividad", "mcp_analizar_datos"}
            and self.ultimas_herramientas[0].get("estado") == "ok"
            and self.ultimas_herramientas[0].get("salida")
        ):
            respuesta = str(self.ultimas_herramientas[0]["salida"]).strip()
            return self._guardar_respuesta_verificada(respuesta, mensajes)

        # El contexto documental y el resumen de memoria se reconstruyen en
        # cada turno; no deben acumularse en el historial ni en la memoria.
        self._mensajes = [
            mensaje
            for mensaje in mensajes
            if getattr(mensaje, "type", "") != "system"
        ]
        self.memory.update(self._mensajes)
        return self._extraer_texto(mensajes[-1])

    def reiniciar(self) -> None:
        self._mensajes = []
        self.agentes_invocados = []
        self.ultimas_herramientas = []
        self.ultimo_estado_grafo = {}
        self._documentos = []
        self._imagen_pendiente = None
        self._ultimo_agente = None
        self.modo_efectivo = self.modo

    def __repr__(self) -> str:
        return (
            f"<Orquestador | modo: {self.modo} | modelo: {self.modelo} | "
            f"tools MCP: {len(self.herramientas)}>"
        )


orquestador = Orquestador()
