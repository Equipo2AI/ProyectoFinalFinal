# Orquestador: adaptador entre la UI y el grafo explícito de LangGraph.

from __future__ import annotations

import logging
import os
import re
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

    def registrar_contexto_consultable(self, documento):
        """Reutiliza el índice temporal firmado; no incorpora archivos al RAG."""
        from proyecto.security.document_access import authorize_context, binding
        authorize_context(documento)
        reference = documento['document_id']
        self.registrar_adjuntos_chat([reference])
        self._document_scope = binding()
        self._document_references.add(reference)
        self._documentos = [d for d in self._documentos if d.get('doc_id') != reference]
        self._documentos.append({'doc_id': reference, 'nombre': documento['name'], 'scope': binding(),
                                'texto': '', 'ruta': '', 'operacion': 'consulta', 'index': documento})

    def contextos_consultables(self):
        from proyecto.security.document_access import authorize_context, binding
        result = []
        for item in self._documentos:
            if item.get('index'):
                if item.get('scope') != binding():
                    raise PermissionError('Contexto de otra sesión.')
                authorize_context(item['index'])
                result.append(item['index'])
        return result

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

    def registrar_intercambio(self, consulta, respuesta):
        """Un solo historial por sesión, incluso para respuestas directas del servicio."""
        from proyecto.security.document_access import binding, authorize_file
        if self._document_scope and self._document_scope != binding():
            raise PermissionError('Contexto de otra sesión.')
        for ref in self._document_references:
            authorize_file(ref)
        if (len(self._mensajes) >= 2 and self._extraer_texto(self._mensajes[-2]) == consulta
                and self._extraer_texto(self._mensajes[-1]) == respuesta):
            return
        self._mensajes.extend([HumanMessage(content=consulta), AIMessage(content=respuesta)])
        self._limitar_historial()

    def redactar_concierge(self, consulta, hechos, evaluacion=None):
        from proyecto.security.local_auth import principal
        from proyecto.security.document_access import authorize_file, binding
        from proyecto.orchestration.perfil_respuesta import redactar_respuesta, detectar_perfil
        from proyecto.orchestration.grafo import _normalizar_historial
        if self._document_scope and self._document_scope != binding():
            raise PermissionError('Contexto de otra sesión.')
        for ref in self._document_references:
            authorize_file(ref)
        role = principal()['role']
        respaldo = '\n'.join(hechos)
        if role != 'normal':
            return respaldo
        public = ({'concierge': {'requisitos': [{k: r.get(k) for k in ('requisito', 'estado', 'cumple', 'aplica', 'incidencias')}
                                               for r in evaluacion.get('requisitos', [])],
                                 'completo_segun_reglas': evaluacion.get('completo_segun_reglas')}} if evaluacion else {})
        context = self.contexto_conversacional()
        public['capacidades_documentales'] = context.get('capacidades_documentales', [])
        text = redactar_respuesta(consulta, respaldo, detectar_perfil(role, consulta), public, self.modelo,
                                  historial=_normalizar_historial(self._historial_para_modelo()), hechos_protegidos=hechos,
                                  contexto=context, diagnostico=(diagnostic := {}))
        self.ultimo_diagnostico_redaccion = diagnostic
        for ref in self._document_references:
            authorize_file(ref)
        if self._document_scope and self._document_scope != binding():
            raise PermissionError('Cambio la autorización durante la respuesta.')
        return text

    @staticmethod
    def hecho_requisito(row):
        label = {'ine': 'la identificación', 'curp': 'la constancia CURP',
                 'domicilio': 'el comprobante de domicilio', 'rfc': 'el RFC'}[row['requisito']]
        if any(i.get('estado') == 'VENCIDO' for i in row.get('incidencias', [])):
            return f"{label.capitalize()} está vencida según la fecha declarada."
        if row.get('aplica') is False:
            return f"No se requiere {label} según las condiciones comprobadas de esta revisión."
        if row.get('estado') == 'VALIDO_SEGUN_REGLAS':
            return f"{label.capitalize()} cumple las comprobaciones documentales de esta revisión."
        if row.get('estado') == 'FALTANTE':
            return f"Falta presentar {label}."
        return f"No se pudo verificar {label}. {row.get('explicacion', '')}"

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
        from proyecto.security.document_access import authorize_file, binding
        attached = getattr(self, '_adjuntos_chat', {})
        excluded = {ref for choice in getattr(self, '_expediente_selecciones', {}).values()
                    for ref in choice['candidatos'] if ref != choice['elegida']}
        refs = [ref for ref in attached if ref not in excluded]
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
            from proyecto.orchestration.interpretacion import _normalizar
            turn = (getattr(self, '_lectura_activa', None) or {}).get('turno', {})
            missing_focus = (turn.get('foco') == 'pendientes' if turn else
                             bool(re.search(r'\bfalt\w*\b', _normalizar(consulta))))
            lines = []
            self._aclaracion_pendiente = None
            for row in result['requisitos']:
                if row.get('aplica') is not False and (not missing_focus or row.get('cumple') is not True):
                    lines.append(self.hecho_requisito(row))
                    chosen = getattr(self, '_expediente_selecciones', {}).get(row['requisito'], {}).get('elegida')
                    if chosen:
                        lines.append(f"Para esta revisión se utilizó {attached[chosen]}.")
                candidates = list(dict.fromkeys(e['documento_id'] for e in row.get('evidencias', [])))
                if len(candidates) > 1 and self._aclaracion_pendiente is None:
                    for ref in candidates:
                        authorize_file(ref)
                        if ref not in refs:
                            raise PermissionError('Candidato fuera del expediente autorizado.')
                    self._aclaracion_pendiente = {'tipo': 'version_documental', 'requisito': row['requisito'],
                                                  'documentos': candidates, 'binding': binding()}
                    lines.append(self._pregunta_version_documental())
            if not lines:
                lines.append('No faltan requisitos documentales por resolver según esta revisión.')
            lines.append('Esta revisión no certifica autenticidad ni titularidad y no equivale a aprobación bancaria.')
            text = self.redactar_concierge(consulta, lines, result)
        self._expediente_en_curso = True
        self._ultimo_agente = 'document_intelligence'
        self.ultimas_herramientas = [{'name': 'evaluar_expediente_micuenta', 'args': {'documentos': refs},
                                     'salida': raw, 'estado': 'ok' if result.get('code') == 'OK' else 'error'}]
        self.agentes_invocados = ['document_intelligence']
        self.ultimo_estado_grafo = {'respuesta': text, 'expediente': result}
        if result.get('code') == 'OK':
            self._resultado_documental = {'scope': binding(), 'publico': {
                'fecha_evaluacion': result.get('fecha_evaluacion'),
                'completo_segun_reglas': result.get('completo_segun_reglas'),
                'requisitos': [{k: row.get(k) for k in ('requisito', 'estado', 'cumple', 'aplica', 'incidencias')}
                               for row in result['requisitos']]}}
        self.registrar_intercambio(consulta, text)
        return text

    def _pregunta_version_documental(self):
        pending = self._aclaracion_pendiente
        names = [self._adjuntos_chat[ref].replace('\n', ' ').replace('\r', ' ')
                 for ref in pending['documentos']]
        return (f"¿Qué versión de {pending['requisito']} deseas utilizar?\n"
                + '\n'.join(f'{i}. {name}' for i, name in enumerate(names, 1))
                + '\nResponde con el nombre del archivo o su posición, por ejemplo «la segunda».')

    def _resolver_version_documental(self, consulta):
        from proyecto.security.document_access import authorize_file, binding
        from proyecto.orchestration.interpretacion import _normalizar
        pending = self._aclaracion_pendiente
        if pending['binding'] != binding():
            raise PermissionError('La selección pertenece a otra sesión.')
        candidates = pending['documentos']
        for ref in candidates:
            authorize_file(ref)
            if ref not in self._adjuntos_chat:
                raise PermissionError('Documento fuera de la conversación.')
        # Un identificador enviado por el cliente nunca sustituye el registro.
        for reference in re.findall(r'\bdoc_[0-9a-f]{48}\b', consulta):
            authorize_file(reference)
            if reference not in candidates:
                raise PermissionError('Referencia fuera de las opciones autorizadas.')
        text = _normalizar(consulta).strip()
        selected = set()
        for i, ref in enumerate(candidates):
            name = _normalizar(self._adjuntos_chat[ref])
            pattern = r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])'
            if re.search(pattern, text):
                selected.add(i)
        # No interpretar los números de un nombre de archivo como posiciones.
        ordinal_text = text
        for ref in candidates:
            ordinal_text = ordinal_text.replace(_normalizar(self._adjuntos_chat[ref]), '')
        ordinals = [('primera', 'primero', 'primer'), ('segunda', 'segundo'),
                    ('tercera', 'tercero', 'tercer'), ('cuarta', 'cuarto'),
                    ('quinta', 'quinto'), ('sexta', 'sexto'), ('septima', 'septimo'), ('octava', 'octavo')]
        for i, words in enumerate(ordinals):
            if re.search(r'\b(?:' + '|'.join(words) + r'|' + str(i + 1) + r')\b', ordinal_text):
                selected.add(i)
        if len(selected) == 1 and next(iter(selected)) < len(candidates):
            chosen = candidates[next(iter(selected))]
            authorize_file(chosen)
            choices = dict(getattr(self, '_expediente_selecciones', {}))
            previous = choices.get(pending['requisito'], {}).get('candidatos', [])
            choices[pending['requisito']] = {'elegida': chosen, 'candidatos': list(dict.fromkeys(previous + candidates))}
            self._expediente_selecciones = choices
            self._ultimo_requisito_seleccionado = pending['requisito']
            self._aclaracion_pendiente = None
            return self._evaluar_expediente_chat(consulta)
        response = self.redactar_concierge(consulta, ['No pude identificar una única opción.', self._pregunta_version_documental()])
        self.ultimas_herramientas = []
        self.agentes_invocados = []
        self.ultimo_estado_grafo = {'respuesta': response}
        self._mensajes.extend([HumanMessage(content=consulta), AIMessage(content=response)])
        self._limitar_historial()
        return response

    def contexto_conversacional(self) -> dict:
        """Proyección mínima de la memoria existente; nunca contiene texto de PDF."""
        from proyecto.security.local_auth import principal
        from proyecto.security.document_access import binding, authorize_file
        from proyecto.agents.document_requirements import requirements_matrix
        from proyecto.orchestration.grafo import _normalizar_historial
        principal()
        scope = binding()
        if getattr(self, '_conversacion_scope', scope) != scope:
            raise PermissionError('La conversación pertenece a otra sesión.')
        self._conversacion_scope = scope
        if self._document_scope and self._document_scope != scope:
            raise PermissionError('Contexto de otra sesión.')
        for ref in self._document_references:
            authorize_file(ref)
        attached = getattr(self, '_adjuntos_chat', {})
        aliases = {ref: f'D{i}' for i, ref in enumerate(attached, 1)}
        opening = (getattr(self, '_apertura_contexto', {})
                   if getattr(self, '_apertura_scope', None) == scope else {})
        pending = self._aclaracion_pendiente or {}
        pending = pending.get('version_documental_pausada', pending)
        result = getattr(self, '_resultado_documental', {})
        if result.get('scope') != scope:
            result = {}
        matrix = requirements_matrix()
        indexes = {d['document_id']: d for d in self.contextos_consultables()}
        return {
            'rol_autenticado': principal()['role'],
            'capacidades_documentales': [r['id'] for r in matrix['requisitos'] if r['id'] in {'ine', 'curp', 'domicilio'}],
            'usar_documentos': getattr(self, '_usar_documentos', True),
            'producto_documentado': matrix['producto'],
            'requisitos_documentados': {'version': matrix['version'], 'canal': matrix['canal'],
                                       'reglas': matrix['requisitos']},
            'producto_confirmado': opening.get('producto') if opening.get('confirmado', True) else None,
            'producto_mencionado': opening.get('producto'),
            'producto_propuesto': opening.get('producto_propuesto'),
            'expediente_en_curso': bool(getattr(self, '_expediente_en_curso', False)),
            'historial_reciente': _normalizar_historial(self._historial_para_modelo()),
            'documentos': [{'alias': aliases[ref], 'nombre': name,
                            **({'metadatos': {k: indexes[ref].get('metadata', {}).get(k) for k in ('type', 'facts')},
                                'consultable': True} if ref in indexes else {})} for ref, name in attached.items()],
            'consulta_documental_pendiente': pending.get('consulta') if pending.get('tipo') == 'consulta_documental' else None,
            'selecciones': {key: aliases.get(value['elegida']) for key, value in
                            getattr(self, '_expediente_selecciones', {}).items()},
            'aclaracion_pendiente': {'tipo': pending.get('tipo'), 'requisito': pending.get('requisito'),
                                    'opciones': [aliases.get(ref) for ref in pending.get('documentos', [])]},
            'resultado_anterior': result.get('publico'),
            'resultado_es_actual': False,  # Solo orienta la intención; el backend vuelve a comprobarlo.
            'visualizacion_anterior': ({k: getattr(self, '_ultima_visualizacion', {}).get(k)
                for k in ('datos', 'grafica_especificacion', 'consultado_en', 'consulta_analitica')}
                if getattr(self, '_visualizacion_scope', None) == scope else None),
            'interpretacion_turno': {k: v for k, v in (getattr(self, '_lectura_activa', None) or {}).get('turno', {}).items()
                                     if k != 'necesidades'},
        }

    def interpretar_turno(self, consulta: str) -> dict:
        """Reutiliza una sola planificación de MiMo después de autorizar el turno."""
        from proyecto.security.local_auth import authorize_request
        from proyecto.security.document_access import binding, authorize_file
        from proyecto.orchestration.grafo import _plan_llm, _pistas_plan, fuera_de_alcance, _solicita_grafica
        user = authorize_request(consulta)
        # No convertir una referencia de archivo en una instrucción al modelo.
        for ref in re.findall(r'\bdoc_[0-9a-f]{48}\b', consulta):
            authorize_file(ref)
        reading = {'consulta': consulta, 'scope': binding(), 'turno': {}, 'plan': None, 'intentado': False}
        signals = _pistas_plan(consulta, ultimo_agente=self._ultimo_agente)
        analytic_followup = bool(getattr(self, '_ultima_visualizacion', None)) and not any(signals.get(k) for k in ('metadata', 'normativa', 'documento'))
        analytic_chart = _solicita_grafica(consulta) and not signals.get('metadata') and (signals.get('analitica') or signals.get('bancaria'))
        if (user['role'] != 'normal' and not (analytic_chart or analytic_followup)) or fuera_de_alcance(consulta, hay_contexto=bool(self._mensajes)):
            return reading
        context = self.contexto_conversacional()
        reading['intentado'] = True
        plan = _plan_llm(consulta, _pistas_plan(consulta, ultimo_agente=self._ultimo_agente), self.modelo,
                         contexto_conversacional=context, lectura_turno=reading['turno'])
        # Revalidar tras la inferencia; un permiso revocado no publica el contexto.
        self.contexto_conversacional()
        if reading['scope'] != binding():
            raise PermissionError('Cambió la autorización durante la interpretación.')
        reading['plan'] = plan if reading['turno'] else None
        if reading['plan']:
            self._validar_fuentes_turno(consulta, reading, context)
        self.ultima_interpretacion_conversacional = dict(reading['turno'])
        self.ultimo_diagnostico_plan = 'llm_contextual' if reading['plan'] else 'respaldo_plan_invalido'
        if not reading['plan']:
            logging.getLogger(__name__).warning('conversacion: respaldo_plan_invalido')
        return reading

    def _validar_fuentes_turno(self, consulta, reading, context):
        """La interpretación propone necesidades; la autoridad de la fuente es del servidor."""
        from proyecto.orchestration.comun import _es_consulta_normativa
        turn = reading['turno']
        needs = turn.get('necesidades')
        if not needs:
            return  # Compatibilidad con lectores internos anteriores; el contrato LLM sí las exige.
        matrix = context['requisitos_documentados']
        known = context['producto_documentado']
        keys = {r['id'] for r in matrix['reglas']}
        # La presencia del producto en el catálogo no significa que el usuario lo eligió.
        # La lectura determinista usa solo texto/contexto del servidor, nunca
        # el producto que esta misma planificación acaba de proponer.
        from proyecto.orchestration.interpretacion import interpretar_consulta
        opening = {'activa': bool(context.get('producto_confirmado') or context.get('producto_propuesto')
                                  or context.get('producto_mencionado')),
                   'producto': context.get('producto_confirmado') or context.get('producto_mencionado'),
                   'producto_propuesto': context.get('producto_propuesto')}
        literal = interpretar_consulta(consulta, contexto_apertura=opening, producto_apertura=known,
                    hay_archivos=bool(context['documentos']), expediente_activo=context.get('expediente_en_curso', False))
        explicit = literal.get('slots', {}).get('producto')
        product_bound = (turn['referencia_clara'] and turn['producto'] == known
                         and (context.get('producto_confirmado') == known or explicit == known or
                              (context.get('producto_mencionado') == known and turn['acto'] == 'seguimiento') or
                              (context.get('producto_propuesto') == known and turn['objetivo'] == 'requisitos') or
                              (turn['acto'] == 'confirmacion' and context.get('producto_propuesto') == known)))
        if turn['acto'] == 'correccion' and explicit != known:
            product_bound = False
        reading['producto_vinculado'] = product_bound
        reading['producto_confirmado'] = bool(product_bound and (
            context.get('producto_confirmado') == known or turn['acto'] == 'confirmacion'))
        if (product_bound and context.get('expediente_en_curso') and context['documentos']
                and literal.get('slots', {}).get('intencion_apertura') == 'expediente'
                and all(n['tipo'] in {'requisito_producto', 'conversacion'} for n in needs)):
            needs = [{'tipo': 'evaluacion_documental', 'consulta': consulta}]
            turn.update(objetivo='expediente', requisito=None,
                        foco='pendientes' if literal['slots'].get('consulta_faltantes') else 'general')
        # Un objetivo de requisitos no puede eludir la autoridad de la matriz
        # etiquetándose como conversación, ni confirmar el catálogo por sí solo.
        general_opening = (not product_bound and (turn['objetivo'] == 'apertura' or
                           (literal.get('slots', {}).get('intencion_apertura') == 'apertura_general'
                            and turn['objetivo'] == 'requisitos')))
        if general_opening:
            needs = [{'tipo': 'conversacion', 'consulta': consulta}]
            turn.update(objetivo='apertura', producto=None, referencia_clara=False)
        elif turn['objetivo'] in {'apertura', 'requisitos'} and all(n['tipo'] == 'conversacion' for n in needs):
            if product_bound:
                needs = [{'tipo': 'requisito_producto', 'consulta': consulta}]
            else:
                turn.update(objetivo='apertura', producto=None, referencia_clara=False)
        # Un título del catálogo compartido exige recuperación. No se buscan ni
        # abren PDFs privados aquí; sus referencias siguen la ruta autorizada.
        from proyecto.agents.local_documents import ROOT
        from proyecto.orchestration.interpretacion import _normalizar
        normalized = _normalizar(consulta)
        catalog = [p for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in {'.pdf', '.txt'}] if ROOT.is_dir() else []
        requested = [p.name for p in catalog if len(p.stem) >= 6 and _normalizar(p.stem) in normalized]
        private_names = {n.casefold() for n in getattr(self, '_adjuntos_chat', {}).values()}
        requested = [n for n in requested if n.casefold() not in private_names]
        if (any(n['tipo'] == 'requisito_producto' for n in needs)
                and not any(_normalizar(n) in normalized for n in requested)
                and not re.search(r'\b(?:documento|manual|archivo|texto|apartado)\b', normalized)):
            requested = []  # El nombre de un producto no selecciona su PDF.
        from proyecto.orchestration.grafo import _es_seguimiento, _solicita_grafica
        from proyecto.orchestration.perfil_respuesta import consulta_documental_contextual
        document_followup = (self._ultimo_agente == 'normatividad_rag'
                             and (_es_seguimiento(consulta) or consulta_documental_contextual(consulta, 'contexto') != consulta))
        if document_followup and not requested and all(n['tipo'] in {'conversacion', 'fundamento_normativo', 'aclaracion'} for n in needs):
            previous = next((m['content'].split('Referencias:', 1)[1]
                             for m in reversed(context['historial_reciente'])
                             if m['role'] == 'assistant' and 'Referencias:' in m['content']), '')
            inherited = [p.name for p in catalog if _normalizar(p.name) in _normalizar(previous)]
            # Una aclaración del modelo no invalida una referencia documental
            # inequívoca del historial. Con varias fuentes no elegir por el usuario.
            if not any(n['tipo'] == 'aclaracion' for n in needs) or len(inherited) == 1:
                requested = inherited
        if (requested or ((document_followup or _es_consulta_normativa(consulta))
                          and all(n['tipo'] == 'conversacion' for n in needs))):
            needs = [{'tipo': 'fundamento_normativo', 'consulta': consulta,
                      **({'documentos': requested} if requested else {})}]
            turn.update(objetivo='normativa', requisito=None)
            if len(requested) == 1:
                turn['referencia_clara'] = True
        turn['necesidades'] = needs
        types = {n['tipo'] for n in needs}
        from proyecto.agents.document_questions import external_request
        financial_pdf = any(d.get('consultable') and d.get('metadatos', {}).get('type') in
                            {'debito', 'credito', 'estado_cuenta'} for d in context['documentos'])
        if (context.get('rol_autenticado') == 'normal' and financial_pdf
                and context.get('usar_documentos', True)
                and not external_request(consulta, hay_documento=True)
                and (turn.get('objetivo') in {'documento', 'datos'} or context.get('consulta_documental_pendiente')
                     or bool(turn.get('visualizacion')) or _solicita_grafica(consulta))
                and types <= {'datos', 'conversacion', 'aclaracion', 'evaluacion_documental'}):
            document_query = next((n['consulta'] for n in needs if n['tipo'] == 'evaluacion_documental'), consulta)
            needs = [{'tipo': 'evaluacion_documental', 'consulta': document_query}]
            turn.update(objetivo='documento', requisito=None, fuente_documental=True)
            turn['necesidades'] = needs
            types = {'evaluacion_documental'}
        uncertain = ('aclaracion' in types or
                     ('requisito_producto' in types and (not product_bound or turn.get('requisito') not in keys | {None})) or
                     ('evaluacion_documental' in types and not context['documentos']))
        # Si el propio historial sitúa el seguimiento en un requisito del producto,
        # una propuesta RAG sin señal normativa independiente no es evidencia suficiente.
        if (types == {'fundamento_normativo'} and product_bound and turn.get('requisito') in keys
                and turn['acto'] in {'seguimiento', 'aclaracion', 'confirmacion'}
                and not _es_consulta_normativa(consulta)):
            uncertain = True
        if uncertain:
            reading['fuentes'] = [{'tipo': 'aclaracion', 'consulta': consulta}]
            turn.update(objetivo='aclarar', referencia_clara=False)
            reading['plan'] = (['directo'], [], '')
        else:
            sources, routes = [], []
            route_map = {'requisito_producto': 'normatividad_rag', 'fundamento_normativo': 'normatividad_rag',
                         'evaluacion_documental': 'document_intelligence', 'datos': 'data_analyst', 'conversacion': 'directo'}
            for need in needs:
                if any(s['tipo'] == need['tipo'] and s['consulta'] == need['consulta'] for s in sources):
                    continue
                source = dict(need)
                if need['tipo'] == 'requisito_producto':
                    source.update(id='matriz_producto', version=matrix['version'])
                sources.append(source)
                route = route_map[need['tipo']]
                if route not in routes:
                    routes.append(route)
            reading['fuentes'] = sources
            if types == {'requisito_producto'}:
                routes = ['directo']
                turn.update(objetivo='requisitos', foco='requisitos')
            elif types == {'evaluacion_documental'}:
                bulk = (not turn.get('fuente_documental') and not turn.get('requisito') and context['documentos']
                        and (product_bound or context.get('expediente_en_curso')))
                turn['objetivo'] = 'expediente' if turn['objetivo'] == 'expediente' or bulk else 'documento'
            elif types == {'fundamento_normativo'}:
                turn['objetivo'] = 'normativa'
            if len(routes) > 1 and 'directo' in routes:
                routes.remove('directo')
            reading['plan'] = (routes, [n['consulta'] for n in sources], '')
        self.ultima_seleccion_fuentes = [s['tipo'] for s in reading['fuentes']]

    def interpretar_apertura(self, consulta: str, lectura: dict | None = None) -> dict:
        """Reutiliza interpretación y slots; el rol procede solo del backend."""
        from proyecto.security.local_auth import principal
        from proyecto.security.document_access import binding
        from proyecto.agents.document_requirements import requirements_matrix
        from proyecto.orchestration.interpretacion import interpretar_consulta
        if principal()['role'] != 'normal':
            self._apertura_contexto = {}
            return {}
        scope = binding()
        context = getattr(self, '_apertura_contexto', {}) if getattr(self, '_apertura_scope', None) == scope else {}
        result = interpretar_consulta(consulta, contexto_apertura=context,
                                   producto_apertura=requirements_matrix()['producto'],
                                   hay_archivos=bool(getattr(self, '_adjuntos_chat', {})),
                                   expediente_activo=getattr(self, '_expediente_en_curso', False))
        reading = lectura if lectura is not None else getattr(self, '_lectura_activa', None)
        if not reading or not reading.get('plan') or reading.get('consulta') != consulta or reading.get('scope') != scope:
            return result
        turn = reading['turno']
        objective = turn['objetivo']
        # Una solicitud combinada sigue el plan multiagente; no truncarla a Concierge.
        combined = len(reading['plan'][0]) > 1 or len({s['tipo'] for s in reading.get('fuentes', [])}) > 1
        slots = result.setdefault('slots', {})
        if objective not in {'apertura', 'requisitos', 'expediente'} or combined:
            slots.pop('intencion_apertura', None)
            return result
        product = turn['producto'] if turn['referencia_clara'] and reading.get('producto_vinculado', False) else None
        intent = ('expediente' if objective == 'expediente' and product else
                  'requisitos' if product else 'apertura_general')
        slots.update(intencion_apertura=intent, producto=product,
                     consulta_faltantes=turn['foco'] == 'pendientes',
                     contexto_apertura={'activa': True, 'producto': product, 'espera_producto': not bool(product),
                                        'confirmado': bool(reading.get('producto_confirmado')),
                                        'producto_propuesto': requirements_matrix()['producto'] if not product else None})
        result['aclaracion'] = {'necesaria': not bool(product), 'pregunta': '', 'opciones': []}
        return result

    def es_respuesta_version(self, consulta):
        from proyecto.agents.document_questions import external_request
        from proyecto.orchestration.interpretacion import interpretar_consulta
        if (self._aclaracion_pendiente or {}).get('tipo') != 'version_documental':
            return False
        slots = interpretar_consulta(consulta).get('slots', {})
        # Solo las elecciones explícitas omiten la interpretación contextual.
        # Una aclaración pendiente no secuestra cualquier turno posterior.
        from proyecto.orchestration.interpretacion import _normalizar
        text = _normalizar(consulta)
        named = any(_normalizar(name) in text for name in getattr(self, '_adjuntos_chat', {}).values())
        ordinal = re.search(r'\b(?:primer[ao]?|segund[ao]|tercer[ao]?|cuart[ao]|quint[ao]|sext[ao]|septim[ao]|octav[ao])\b', text)
        number = re.fullmatch(r'\s*(?:(?:la|el|opcion)\s+)?[1-8]\s*[.!]?\s*', text)
        reference = re.search(r'\bdoc_[0-9a-f]{48}\b', text)
        opening = self.interpretar_apertura(consulta).get('slots', {}).get('intencion_apertura')
        return bool((named or ordinal or number or reference) and not opening
                    and not slots.get('metrica') and not external_request(consulta))

    def procesar(self, consulta: str, perfil: dict | None = None, *, documentos_adjuntos: list[str] | None = None,
                 lectura_turno: dict | None = None) -> str:
        from proyecto.security.document_access import binding
        if getattr(self, '_conversacion_scope', binding()) != binding():
            raise PermissionError('La conversación pertenece a otra sesión.')
        # El servicio puede compartir la planificación de este mismo turno; nunca
        # aceptar una lectura de otra sesión o pregunta.
        if lectura_turno is not None and (lectura_turno.get('scope') != binding() or lectura_turno.get('consulta') != consulta):
            raise PermissionError('Interpretación fuera de la sesión.')
        selection = self.es_respuesta_version(consulta) and not documentos_adjuntos
        self._lectura_activa = lectura_turno if lectura_turno is not None else (None if selection else self.interpretar_turno(consulta))
        try:
            return self._procesar_con_lectura(consulta, perfil, documentos_adjuntos=documentos_adjuntos)
        finally:
            self._lectura_activa = None

    def _procesar_con_lectura(self, consulta, perfil, *, documentos_adjuntos=None):
        pending = self._aclaracion_pendiente
        document_pending = (pending if pending and pending.get('tipo') == 'version_documental'
                            else (pending or {}).get('version_documental_pausada'))
        preserve = document_pending and not self.es_respuesta_version(consulta)
        text = self._procesar_turno(consulta, perfil, documentos_adjuntos=documentos_adjuntos)
        if preserve and not documentos_adjuntos:
            from proyecto.security.document_access import binding
            if document_pending['binding'] == binding():
                if self._aclaracion_pendiente and self._aclaracion_pendiente.get('tipo') != 'version_documental':
                    self._aclaracion_pendiente['version_documental_pausada'] = document_pending
                else:
                    self._aclaracion_pendiente = document_pending
        return text

    def _procesar_turno(self, consulta: str, perfil: dict | None = None, *, documentos_adjuntos: list[str] | None = None) -> str:
        """Atiende una consulta con el grafo explícito de LangGraph.

        ``perfil`` es el contexto de sesión autenticada (rol y cliente_id).
        """
        from proyecto.security.local_auth import authorize_request
        from proyecto.orchestration.grafo import (
            RESPUESTA_FUERA_DE_ALCANCE,
            fuera_de_alcance,
        )

        from proyecto.security.local_auth import principal
        selection_pending = (self.es_respuesta_version(consulta)
                             and not documentos_adjuntos and principal()['role'] == 'normal')
        user = principal() if selection_pending else authorize_request(consulta)
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
        if selection_pending:
            return self._resolver_version_documental(consulta)
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
        interpretation = self.interpretar_apertura(consulta)
        slots = interpretation.get('slots', {})
        intent = slots.get('intencion_apertura')
        turn = (getattr(self, '_lectura_activa', None) or {}).get('turno', {})
        if turn.get('acto') == 'correccion' and not turn.get('producto'):
            self._apertura_contexto = {}
        if turn.get('objetivo') == 'aclarar':
            facts = ['¿A qué producto, documento o resultado anterior te refieres?']
            if (self._aclaracion_pendiente or {}).get('tipo') == 'version_documental':
                facts = [self._pregunta_version_documental()]
            text = self.redactar_concierge(consulta, facts)
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self.ultimo_estado_grafo = {'respuesta': text, 'aclaracion': {'necesaria': True}}
            self.registrar_intercambio(consulta, text)
            return text
        if documentos_adjuntos:
            # Los adjuntos de este envío son un dato del servicio, no una
            # afirmación del texto del usuario. Nunca aceptar rutas sin registro.
            for reference in documentos_adjuntos:
                identifier, _ = authorize_file(reference)
                if identifier not in getattr(self, '_adjuntos_chat', {}):
                    raise PermissionError('Adjunto no registrado en esta conversación.')
            context = (getattr(self, '_apertura_contexto', {})
                       if getattr(self, '_apertura_scope', None) == binding() else {})
            product = slots.get('producto') or context.get('producto')
            turn = (getattr(self, '_lectura_activa', None) or {}).get('turno', {})
            if (user['role'] == 'normal' and (product or getattr(self, '_expediente_en_curso', False))
                    and (not turn or turn['objetivo'] == 'expediente')):
                from proyecto.agents.document_requirements import requirements_matrix
                intent = 'expediente'
                slots.update(producto=product or requirements_matrix()['producto'], intencion_apertura=intent,
                             contexto_apertura={'activa': True, 'producto': product or requirements_matrix()['producto'],
                                                'espera_producto': False})
            else:
                text = (f'Recibí {len(documentos_adjuntos)} PDF y quedaron registrados para esta conversación. '
                        'Indica qué análisis necesitas o qué documento deseas consultar.')
                if user['role'] == 'normal':
                    text += ' Si deseas evaluar requisitos de apertura, indica el producto; puedo evaluar MiCuenta Banamex.'
                    text = self.redactar_concierge(consulta, [text])
                self.ultimas_herramientas = []
                self.agentes_invocados = []
                self.ultimo_estado_grafo = {'respuesta': text, 'documentos_recibidos': len(documentos_adjuntos)}
                self.registrar_intercambio(consulta, text)
                return text
        if intent:
            self._apertura_contexto = slots['contexto_apertura']
            self._apertura_scope = binding()
            self._aclaracion_pendiente = ({'consulta': consulta, 'tipo': 'apertura',
                                          **interpretation['aclaracion']}
                                         if interpretation['aclaracion']['necesaria'] and intent != 'expediente' else None)
        if intent in {'apertura_general', 'requisitos'}:
            from proyecto.agents.document_requirements import requirements_matrix
            matrix = requirements_matrix()
            if intent == 'apertura_general':
                lines = [f"Puedo orientarte sobre la apertura de {matrix['producto']} en {matrix['canal']}.",
                         'Puedo explicar los requisitos y revisar tus PDF propios autorizados antes de acudir al banco; '
                         'no puedo abrir ni aprobar la cuenta desde el chat.',
                         'Los requisitos dependen de la cuenta y del canal. ¿Te interesa esa cuenta u otro producto?']
            else:
                lines = [f"Para abrir {matrix['producto']} en {matrix['canal']}:",
                         'Estos requisitos corresponden a persona física mexicana sin actividad empresarial; '
                         'no he confirmado que ese sea tu perfil.',
                         'Se explican condiciones generales del producto; esta respuesta no acredita haber revisado una INE.']
                labels = {'ine': 'Identificación oficial INE vigente', 'domicilio': 'Comprobante de domicilio',
                          'curp': 'Constancia CURP separada', 'rfc': 'RFC'}
                conditions = {'siempre': 'Obligatorio dentro de ese perfil',
                              'ine_no_incluye_curp': 'Condicional: si la CURP no figura en la INE',
                              'actividad_empresarial': 'Condicional: cuando aplique actividad empresarial; ese perfil no se evalúa en esta etapa'}
                for rule in matrix['requisitos']:
                    detail = ''
                    if rule['id'] == 'domicilio':
                        receipts = ', '.join(t.replace('_', ' ') for t in rule['tipos_recibo'])
                        detail = (f" Recibos contemplados: {receipts}; emisión no futura y antigüedad máxima "
                                  f"de {rule['antiguedad_maxima_meses']} meses calendario.")
                    lines.append(f"- {labels[rule['id']]}: {conditions[rule['condicion']]}.{detail}")
                for source in matrix['fuentes']:
                    lines.append(f"Fuente: Banamex, {source['url']}; consultada el {source['fecha_consulta']}.")
                lines.append('Puedes solicitar la revisión de tus PDF autorizados en esta conversación. '
                             'Cumplir estos requisitos no certifica autenticidad ni aprobación de apertura.')
                if slots.get('consulta_faltantes'):
                    lines.append('Sin evaluar tus documentos no puedo afirmar cuál te falta. '
                                 'Un requisito ausente impide declarar completo el expediente; puedes adjuntar tus PDF para revisarlos.')
            text = self.redactar_concierge(consulta, lines)
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self.ultimo_estado_grafo = {'respuesta': text, 'interpretacion': interpretation, 'fuente_estructurada': {
                'id': matrix['id'], 'version': matrix['version'], 'fuentes': matrix['fuentes']}}
            self.registrar_intercambio(consulta, text)
            return text
        if intent == 'aclaracion_expediente':
            text = '¿Para qué trámite necesitas revisar los documentos? Indica si es apertura de MiCuenta y utiliza los PDF de esta conversación.'
            self.ultimas_herramientas = []
            self.agentes_invocados = []
            self.ultimo_estado_grafo = {'respuesta': text}
            self._mensajes.extend([HumanMessage(content=consulta), AIMessage(content=text)])
            return text
        if intent == 'expediente':
            return self._evaluar_expediente_chat(consulta)
        if (self._aclaracion_pendiente or {}).get('tipo') == 'apertura':
            self._aclaracion_pendiente = None
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
        from proyecto.security.document_access import binding
        reading = getattr(self, '_lectura_activa', None) or {}
        contextual_plan = None
        if reading.get('plan'):
            from proyecto.orchestration.interpretacion import _resultado
            contextual_plan = {'rutas': reading['plan'][0], 'subpreguntas': reading['plan'][1],
                               'visualizacion': reading['turno'].get('visualizacion'),
                               'fuentes': reading.get('fuentes', []),
                               'interpretacion': _resultado(consulta, slots={}, lectura='Interpretación contextual'),
                               'contexto': {k: v for k, v in self.contexto_conversacional().items() if k != 'historial_reciente'}}

        consulta_grafo = consulta
        if not contextual_plan and self._aclaracion_pendiente and self._aclaracion_pendiente.get('tipo') != 'version_documental' and es_respuesta_de_aclaracion(consulta):
            consulta_grafo = completar_consulta(
                str(self._aclaracion_pendiente.get("consulta") or ""),
                consulta,
            )

        historial = ([*self._historial_para_modelo(), HumanMessage(content=consulta_grafo)] if self._document_scope else
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
            plan_conversacional=contextual_plan,
            planificacion_intentada=bool(reading.get('intentado')),
            analitica_previa=(getattr(self, '_ultima_visualizacion', None)
                             if getattr(self, '_visualizacion_scope', None) == binding() else None),
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
        analytic = (estado.get('resultados') or {}).get('data_analyst') or {}
        if analytic.get('grafica'):
            self._ultima_visualizacion = {k: analytic.get(k) for k in
                ('datos', 'sql', 'respuesta', 'respuesta_base', 'consultado_en', 'grafica_especificacion', 'consulta_analitica')}
            self._visualizacion_scope = binding()
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
        if plan == ['directo'] and reading.get('plan'):
            from proyecto.security.local_auth import principal
            from proyecto.orchestration.interpretacion import _normalizar
            known = self.contexto_conversacional()['producto_documentado']
            if (principal()['role'] == 'normal' and not getattr(self, '_apertura_contexto', {}).get('producto')
                    and _normalizar(known) in _normalizar(respuesta)):
                self._apertura_scope = binding()
                self._apertura_contexto = {'activa': True, 'producto': None, 'confirmado': False,
                                          'espera_producto': True, 'producto_propuesto': known}
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
        self._ultima_visualizacion = None
        self._visualizacion_scope = None
        self.__dict__.pop('_conversacion_scope', None)
        self._resultado_documental = {}
        self._lectura_activa = None
        self._ultimo_requisito_seleccionado = None
        self._expediente_selecciones = {}
        self._apertura_contexto = {}
        self._apertura_scope = None
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
