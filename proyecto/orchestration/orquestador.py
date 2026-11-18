# Orquestador: adaptador entre la UI y el grafo explícito de LangGraph.

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import AIMessage, HumanMessage

from proyecto.agents import (
    data_analyst_agent,
    data_steward_agent,
    document_intelligence_agent,
    normatividad_rag_agent,
)
from proyecto.config.models import modelo_de
from proyecto.memory.document_context import DocumentContextStore
from proyecto.memory.long_term import LongTermMemory, config_from_file
from proyecto.orchestration.comun import (
    _ruta_documento_de_consulta,
    _ruta_imagen_de_consulta,
)
from proyecto.orchestration.perfil_respuesta import detectar_perfil

logger = logging.getLogger(__name__)

_MARCADOR_CONTEXTO = "[CONTEXTO_DOCUMENTOS]"

AGENTES = {
    "data_steward": data_steward_agent,
    "normatividad_rag": normatividad_rag_agent,
    "data_analyst": data_analyst_agent,
    "document_intelligence": document_intelligence_agent,
}


def _entero_env(nombre: str, defecto: int, minimo: int = 1) -> int:
    try:
        return max(minimo, int(str(os.getenv(nombre, defecto)).strip()))
    except (TypeError, ValueError):
        return defecto


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
        usuario: str | None = None,
        document_store: DocumentContextStore | None = None,
    ):
        self.nombre = "orquestador"
        self.modelo = modelo or modelo_de("orchestrator")
        self.usuario = str(
            usuario or os.getenv("PROYECTO_AUTH_SUBJECT") or "local"
        ).strip() or "local"
        self.agentes_invocados: list[str] = []  # trazas de la última consulta
        self.ultimas_herramientas: list[dict] = []  # nombre, args, salida, estado
        self.ultimo_estado_grafo: dict = {}  # resultados estructurados del turno
        self.ultimo_perfil_respuesta = None  # estilo usado en la última respuesta
        self.modo_efectivo = "grafo"
        self._mensajes: list = []  # historial de la conversación
        self._documentos: list[dict] = []  # caché de la sesión actual
        self._document_store = document_store
        self._imagen_pendiente: str | None = None
        self._ultimo_agente: str | None = None
        if memory is None:
            self.memory = LongTermMemory(
                model=modelo,
                config=replace(config_from_file(), user=self.usuario),
            )
        else:
            self.memory = memory

    @property
    def document_store(self) -> DocumentContextStore:
        """Almacén privado perezoso para no crear archivos al importar."""
        if self._document_store is None:
            self._document_store = DocumentContextStore()
        return self._document_store

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
        self._limitar_historial()

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
    ) -> str | None:
        """Guarda el texto de un documento o imagen en el contexto del usuario."""
        texto = (texto or "").strip()
        if not texto or texto.startswith("No se pudo procesar la imagen:"):
            return None

        ruta_resuelta = str(Path(ruta).expanduser().resolve())
        etiqueta = (nombre or Path(ruta_resuelta).name).strip()
        try:
            doc_id = self.document_store.save(
                self.usuario,
                etiqueta,
                operacion,
                texto,
                ruta_resuelta,
            )
        except Exception as error:  # noqa: BLE001 - no romper la respuesta
            logger.warning("No se pudo guardar el contexto documental: %s", error)
            return None

        self._documentos = [
            documento
            for documento in self._documentos
            if documento.get("ruta") != ruta_resuelta
            or documento.get("operacion") != operacion
        ]
        self._documentos.append(
            {
                "doc_id": doc_id,
                "ruta": ruta_resuelta,
                "nombre": etiqueta,
                "operacion": operacion,
            }
        )
        self._documentos = self._documentos[-5:]
        self._ultimo_agente = "document_intelligence"
        if self._imagen_pendiente == ruta_resuelta:
            self._imagen_pendiente = None
        return doc_id

    def _contexto_documentos(self, consulta: str = "") -> str:
        partes: list[str] = []
        contexto = self.document_store.build_context(self.usuario, consulta)
        if contexto:
            partes.append(contexto)

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
            "Contexto documental privado del usuario autenticado. Úsalo como "
            "evidencia para preguntas de seguimiento. No lo envíes a Pinecone, "
            "al índice normativo ni a otros usuarios; no vuelvas a procesar la "
            "imagen salvo que el usuario lo pida.\n\n"
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

    def _historial_para_modelo(self) -> list:
        """Ventana acotada del chat; el contexto documental va aparte."""
        max_mensajes = _entero_env("CHAT_HISTORY_MESSAGES", 12, 2)
        max_caracteres = _entero_env("CHAT_HISTORY_CHARS", 8_000, 1_000)
        seleccion: list = []
        total = 0
        for mensaje in reversed(self._mensajes[-max_mensajes:]):
            if isinstance(mensaje, dict):
                texto = str(mensaje.get("content") or "")
            else:
                texto = self._extraer_texto(mensaje)
            if seleccion and total + len(texto) > max_caracteres:
                break
            seleccion.append(mensaje)
            total += len(texto)
        if not seleccion and self._mensajes:
            seleccion = [self._mensajes[-1]]
        return list(reversed(seleccion))

    def _limitar_historial(self) -> None:
        maximo = _entero_env("CHAT_HISTORY_STORAGE_MESSAGES", 80, 8)
        if len(self._mensajes) > maximo:
            self._mensajes = self._mensajes[-maximo:]

    def procesar(self, consulta: str, perfil: dict | None = None) -> str:
        """Atiende una consulta con el grafo explícito de LangGraph.

        ``perfil`` es el contexto de sesión autenticada (rol y cliente_id).
        """
        from proyecto.security.local_auth import authorize_request
        from proyecto.orchestration.grafo import (
            RESPUESTA_FUERA_DE_ALCANCE,
            fuera_de_alcance,
        )

        user = authorize_request(consulta)
        if fuera_de_alcance(
            consulta,
            hay_contexto=bool(self._mensajes or self._documentos),
            imagen_pendiente=self._imagen_pendiente,
        ):
            # No preparar/actualizar memoria aquí: puede disparar inferencias.
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self.ultimo_estado_grafo = {
                "respuesta": RESPUESTA_FUERA_DE_ALCANCE,
                "verificacion": {"estado": "fuera_de_alcance"},
            }
            self._mensajes.extend(
                [
                    HumanMessage(content=consulta),
                    AIMessage(content=RESPUESTA_FUERA_DE_ALCANCE),
                ]
            )
            return RESPUESTA_FUERA_DE_ALCANCE
        perfil = {
            "rol": "superusuario" if user["role"] == "auditor" else "usuario",
            "cliente_id": user["customers"][0] if user["role"] == "normal" else None,
        }
        # Estilo de redacción: rol del login + lo que pide la consulta.
        self.ultimo_perfil_respuesta = detectar_perfil(user["role"], consulta)
        self.ultimo_estado_grafo = {}
        return self._procesar_grafo(
            consulta,
            perfil=perfil,
            perfil_respuesta=self.ultimo_perfil_respuesta.a_dict(),
        )

    def _procesar_grafo(
        self,
        consulta: str,
        perfil: dict | None = None,
        perfil_respuesta: dict | None = None,
    ) -> str:
        from proyecto.orchestration.grafo import ejecutar_grafo

        historial = self.memory.prepare(self._historial_para_modelo(), consulta)
        estado = ejecutar_grafo(
            consulta,
            historial=historial,
            contexto_documentos=self._contexto_documentos(consulta),
            contexto_analista=self._contexto_analista(),
            modelo=self.modelo,
            imagen_pendiente=self._imagen_pendiente,
            ultimo_agente=self._ultimo_agente,
            perfil=perfil,
            perfil_respuesta=perfil_respuesta,
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
            nombres_imagen = {
                "mcp_extraer_texto_imagen",
                "mcp_describir_imagen",
            }
            if (
                nombre not in nombres_imagen
                and nombre != "mcp_convertir_documento_markdown"
            ) or traza.get("estado") == "error":
                continue

            salida = str(traza.get("salida") or "")
            if nombre == "mcp_convertir_documento_markdown":
                ruta = _ruta_documento_de_consulta(consulta)
                operacion = "markdown"
                if salida.startswith("MarkItDown no extrajo texto"):
                    continue
            else:
                ruta = ruta_documento or _ruta_imagen_de_consulta(consulta)
                operacion = (
                    "descripcion"
                    if nombre == "mcp_describir_imagen"
                    else "ocr"
                )
            if not ruta:
                continue
            self.registrar_documento(
                ruta,
                operacion,
                salida,
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
        self._limitar_historial()
        self.modo_efectivo = "grafo"
        return respuesta

    def reiniciar(self) -> None:
        self._mensajes = []
        self.agentes_invocados = []
        self.ultimas_herramientas = []
        self.ultimo_estado_grafo = {}
        self._documentos = []
        self._imagen_pendiente = None
        self._ultimo_agente = None
        self.modo_efectivo = "grafo"

    def __repr__(self) -> str:
        return f"<Orquestador | modo: grafo | modelo: {self.modelo}>"


orquestador = Orquestador()
