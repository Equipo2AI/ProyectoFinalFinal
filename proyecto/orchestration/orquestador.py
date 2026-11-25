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
from proyecto.agents.document_relevance import es_rechazo
from proyecto.config.models import modelo_de
from proyecto.memory.document_context import DocumentContextStore
from proyecto.memory.long_term import LongTermMemory, config_from_file
from proyecto.orchestration.comun import (
    _ruta_documento_de_consulta,
    _ruta_imagen_de_consulta,
)
from proyecto.orchestration.interpretacion import (
    completar_consulta,
    es_respuesta_de_aclaracion,
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
        if document_store is not None:
            raise ValueError('El historial documental persistente no esta habilitado para sesiones privadas.')
        self._document_scope = None
        self._document_references: set[str] = set()
        self._imagen_pendiente: str | None = None
        self._ultimo_agente: str | None = None
        self._aclaracion_pendiente: dict | None = None
        if memory is None:
            self.memory = LongTermMemory(
                model=modelo,
                config=replace(config_from_file(), user=self.usuario),
            )
        else:
            self.memory = memory

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
        from proyecto.security.document_access import authorize_file, binding
        identifier, entry = authorize_file(ruta)
        scope = binding()
        if self._document_scope not in (None, scope):
            self.reiniciar()
        self._document_scope = scope
        self._document_references.add(identifier)
        self._imagen_pendiente = entry['path']
        self._aclaracion_pendiente = None
        self._mensajes = [
            *self._mensajes,
            HumanMessage(content=f"Adjunté una imagen en {self._imagen_pendiente}."),
            AIMessage(content=pregunta),
        ]
        self.agentes_invocados = []
        self.ultimas_herramientas = []
        if self._document_scope is None:
            self.memory.update(self._mensajes)
        self._limitar_historial()

    @property
    def imagen_pendiente(self) -> str | None:
        return self._imagen_pendiente

    def limpiar_imagen_pendiente(self) -> None:
        self._imagen_pendiente = None

    def marcar_imagen_pendiente(self, ruta: str) -> None:
        from proyecto.security.document_access import authorize_file, binding
        identifier, entry = authorize_file(ruta)
        scope = binding()
        if self._document_scope not in (None, scope):
            self.reiniciar()
        self._document_scope = scope
        self._document_references.add(identifier)
        self._imagen_pendiente = entry['path']

    def registrar_documento(
        self,
        ruta: str,
        operacion: str,
        texto: str,
        nombre: str | None = None,
    ) -> str | None:
        """Guarda el texto de un documento o imagen en el contexto del usuario.

        Los archivos rechazados por el filtro de relevancia no se guardan: su
        contenido no debe llegar al contexto de preguntas posteriores.
        """
        texto = (texto or "").strip()
        if not texto or texto.startswith("No se pudo procesar la imagen:"):
            return None

        ruta_resuelta = str(Path(ruta).expanduser().resolve())
        if es_rechazo(texto):
            if self._imagen_pendiente == ruta_resuelta:
                self._imagen_pendiente = None
            return None
        etiqueta = (nombre or Path(ruta_resuelta).name).strip()
        from proyecto.security.document_access import authorize_file, binding, authorize_content
        from proyecto.security.local_auth import principal
        from proyecto.agents.document_questions import _sanitize
        identifier, entry = authorize_file(ruta)
        scope = binding()
        if self._document_scope not in (None, scope):
            self.reiniciar()
        self._document_scope = scope
        self._document_references.add(identifier)
        authorize_content(texto)
        texto = _sanitize(texto, principal()['permissions'])
        doc_id = identifier
        self._documentos = [
            documento
            for documento in self._documentos
            if documento.get("ruta") != ruta_resuelta
            or documento.get("operacion") != operacion
        ]
        self._documentos.append(
            {
                "doc_id": doc_id,
                "texto": texto,
                "scope": scope,
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

    def registrar_documento_rechazado(
        self,
        ruta: str,
        mensaje: str,
        consulta: str = "",
    ) -> None:
        """Deja constancia del rechazo en el hilo sin guardar el archivo.

        Así una pregunta de seguimiento ("¿por qué no lo leíste?") tiene
        contexto, pero el contenido del archivo nunca entra al historial.
        """
        ruta_resuelta = str(Path(ruta).expanduser().resolve())
        if self._imagen_pendiente == ruta_resuelta:
            self._imagen_pendiente = None

        mensaje = (mensaje or "").strip()
        if not mensaje:
            return
        ultimo = self._mensajes[-1] if self._mensajes else None
        if (
            ultimo is not None
            and getattr(ultimo, "type", "") == "ai"
            and es_rechazo(self._extraer_texto(ultimo))
        ):
            # El grafo ya registró este turno; solo se actualiza el texto
            # (por ejemplo, con el nombre original del archivo).
            self._mensajes = [*self._mensajes[:-1], AIMessage(content=mensaje)]
        else:
            peticion = (consulta or "").strip() or "Adjunté un archivo."
            self._mensajes = [
                *self._mensajes,
                HumanMessage(content=peticion),
                AIMessage(content=mensaje),
            ]
        self.agentes_invocados = []
        if self._document_scope is None:
            self.memory.update(self._mensajes)
        self._limitar_historial()

    @staticmethod
    def _rechazo_en_trazas(trazas: list[dict]) -> str | None:
        """Devuelve el primer rechazo por relevancia de las tools del turno."""
        for traza in trazas or []:
            salida = str(traza.get("salida") or "").strip()
            if es_rechazo(salida):
                return salida
        return None

    def _contexto_documentos(self, consulta: str = "") -> str:
        partes: list[str] = []
        from proyecto.security.document_access import authorize_file, binding
        from proyecto.memory.document_context import _secciones_markdown, _seleccionar_secciones
        for reference in self._document_references:
            authorize_file(reference)
        for document in self._documentos:
            authorize_file(document['doc_id'])
            if document['scope'] != binding():
                raise PermissionError('Contexto documental fuera de la sesion actual.')
            sections = _secciones_markdown(document['texto'])
            relevant = _seleccionar_secciones(sections, consulta, 10000)
            partes.append(document['nombre'] + '\n' + (relevant or document['texto'][:10000]))

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

    def registrar_adjuntos_chat(self, documentos):
        """Conserva solo referencias del registro privado; nunca texto sin admisión."""
        from proyecto.security.document_access import authorize_file, binding
        scope = binding()
        if self._document_scope not in (None, scope):
            raise PermissionError('Los adjuntos pertenecen a otra sesión.')
        checked = [authorize_file(ref)[0] for ref in documentos]
        current = dict(getattr(self, '_adjuntos_chat', {}))
        for ref in checked:
            _, entry = authorize_file(ref)
            if Path(entry['path']).suffix.lower() != '.pdf':
                raise ValueError('El expediente admite PDF.')
            current[ref] = entry.get('name', Path(entry['path']).name)
        if len(current) > 8:
            raise ValueError('Utiliza como máximo ocho PDF en la conversación.')
        self._adjuntos_chat = current
        self._document_scope = scope
        self._document_references.update(checked)

    def _evaluar_expediente_chat(self, consulta):
        import json
        from proyecto.mcp_servers.mcp_client import llamar_herramienta
        from proyecto.security.document_access import authorize_file
        attached = getattr(self, '_adjuntos_chat', {})
        named = [ref for ref, name in attached.items() if name.casefold() in consulta.casefold()]
        refs = named or list(attached)
        for ref in refs:
            authorize_file(ref)
        raw = llamar_herramienta('proyecto.mcp_servers.document_intelligence_server',
                                 'evaluar_expediente_micuenta', {'documentos': refs})
        result = json.loads(raw)
        for ref in refs:
            authorize_file(ref)
        if result.get('code') == 'ACCESO_DENEGADO':
            raise PermissionError('Expediente fuera de alcance.')
        if result.get('code') != 'OK' or not isinstance(result.get('requisitos'), list):
            text = 'No se pudo verificar el expediente por un error técnico; no se clasificó como incompleto.'
        else:
            lines = ['Evaluación documental de MiCuenta Banamex:']
            for row in result['requisitos']:
                status = row.get('estado') or ('NO_APLICA' if row.get('aplica') is False else 'NO_VERIFICABLE')
                lines.append(f"- {row['requisito']}: {status}. {row.get('explicacion', '')}")
                if any(i.get('estado') == 'VENCIDO' for i in row.get('incidencias', [])):
                    lines.append('  Incidencia: VENCIDO.')
            lines.append('Esta revisión no certifica autenticidad ni titularidad y no equivale a aprobación bancaria.')
            text = '\n'.join(lines)
        self._expediente_en_curso = True
        self._ultimo_agente = 'document_intelligence'
        self.ultimas_herramientas = [{'name': 'evaluar_expediente_micuenta', 'args': {'documentos': refs},
                                     'salida': raw, 'estado': 'ok' if result.get('code') == 'OK' else 'error'}]
        self.agentes_invocados = ['document_intelligence']
        self.ultimo_estado_grafo = {'respuesta': text, 'expediente': result}
        self._mensajes.extend([HumanMessage(content=consulta), AIMessage(content=text)])
        self._limitar_historial()
        return text

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
        if self._document_scope:
            from proyecto.security.document_access import binding, authorize_file
            try:
                if self._document_scope != binding():
                    raise PermissionError('Cambio la sesion documental.')
                for reference in self._document_references:
                    authorize_file(reference)
            except PermissionError:
                self.reiniciar()
                raise
        from proyecto.security.document_access import authorize_file, binding
        for reference in (self._imagen_pendiente, _ruta_documento_de_consulta(consulta), _ruta_imagen_de_consulta(consulta)):
            if reference:
                named = [ref for ref, name in getattr(self, '_adjuntos_chat', {}).items()
                         if name.casefold() == str(reference).casefold()]
                if len(named) > 1:
                    raise ValueError('Hay varios adjuntos con ese nombre; precisa el documento.')
                if named:
                    reference = named[0]
                identifier, _ = authorize_file(reference)
                self._document_references.add(identifier)
                self._document_scope = binding()
        from proyecto.orchestration.grafo import intencion_micuenta
        intent = intencion_micuenta(consulta, seguimiento=getattr(self, '_expediente_en_curso', False))
        if intent == 'aclaracion_expediente':
            text = '¿Para qué trámite necesitas revisar los documentos? Indica si es apertura de MiCuenta y utiliza los PDF de esta conversación.'
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self.ultimo_estado_grafo = {'respuesta': text}
            self._mensajes.extend([HumanMessage(content=consulta), AIMessage(content=text)])
            return text
        if intent == 'expediente':
            return self._evaluar_expediente_chat(consulta)
        if fuera_de_alcance(
            consulta,
            hay_contexto=bool(self._mensajes or self._documentos),
            imagen_pendiente=self._imagen_pendiente,
        ):
            # No preparar/actualizar memoria aquí: puede disparar inferencias.
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self._aclaracion_pendiente = None
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

        consulta_grafo = consulta
        if self._aclaracion_pendiente and es_respuesta_de_aclaracion(consulta):
            consulta_grafo = completar_consulta(
                str(self._aclaracion_pendiente.get("consulta") or ""),
                consulta,
            )

        historial = (self._historial_para_modelo() if self._document_scope else
                     self.memory.prepare(self._historial_para_modelo(), consulta_grafo))
        estado = ejecutar_grafo(
            consulta_grafo,
            historial=historial,
            contexto_documentos=self._contexto_documentos(consulta_grafo),
            document_references=sorted(self._document_references),
            contexto_analista=self._contexto_analista(),
            modelo=self.modelo,
            imagen_pendiente=self._imagen_pendiente,
            ultimo_agente=self._ultimo_agente,
            perfil=perfil,
            perfil_respuesta=perfil_respuesta,
        )

        from proyecto.security.document_access import authorize_file, binding
        if self._document_scope and self._document_scope != binding():
            self.reiniciar()
            raise PermissionError('Cambio la autorizacion durante la respuesta.')
        for reference in self._document_references:
            authorize_file(reference)
        respuesta = str(estado.get("respuesta") or "").strip()
        if not respuesta:
            raise RuntimeError("El grafo terminó sin respuesta.")

        self.ultimo_estado_grafo = estado
        aclaracion = estado.get("aclaracion") or {}
        if aclaracion.get("necesaria"):
            self._aclaracion_pendiente = {
                "consulta": consulta_grafo,
                "pregunta": str(aclaracion.get("pregunta") or ""),
            }
        else:
            self._aclaracion_pendiente = None
        trazas = list(estado.get("trazas") or [])
        self.ultimas_herramientas = trazas
        self.agentes_invocados = [
            str(traza.get("name"))
            for traza in trazas
            if traza.get("name")
        ]

        # Si el filtro de relevancia rechazó un archivo, la respuesta es el
        # rechazo literal. No se deja al LLM sintetizar: podría describir el
        # archivo o contestar la pregunta con conocimiento general.
        rechazo = self._rechazo_en_trazas(trazas)
        if rechazo:
            respuesta = rechazo
            estado["respuesta"] = rechazo

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
        if self._document_scope is None:
            self.memory.update(self._mensajes)
        self._limitar_historial()
        self.modo_efectivo = "grafo"
        return respuesta

    def reiniciar(self) -> None:
        self._adjuntos_chat = {}
        self._expediente_en_curso = False
        self._mensajes = []
        self.agentes_invocados = []
        self.ultimas_herramientas = []
        self.ultimo_estado_grafo = {}
        self._documentos = []
        self._document_scope = None
        self._document_references.clear()
        self._imagen_pendiente = None
        self._ultimo_agente = None
        self._aclaracion_pendiente = None
        self.modo_efectivo = "grafo"

    def __repr__(self) -> str:
        return f"<Orquestador | modo: grafo | modelo: {self.modelo}>"


orquestador = Orquestador()
