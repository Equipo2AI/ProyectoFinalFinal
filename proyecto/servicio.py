"""Contrato de respuesta entre la UI y el orquestador.

La forma de ``Respuesta`` conserva la estructura que usaba la demo (pasos,
alertas, SQL, datos, gráfica y fuentes) como guía para las tools MCP que
devuelvan resultados estructurados. El flujo real ya no usa datos sintéticos.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import TypedDict

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from proyecto.agents.document_intelligence_agent import (
    EXTENSIONES_DOCUMENTO,
    EXTENSIONES_IMAGEN,
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
    MODELO_VISION_POR_DEFECTO,
    describe_an_image,
    document_to_markdown,
    ocr_image,
)
from proyecto.agents.document_relevance import es_rechazo, filtrar_documento
from proyecto.guardrails import limpiar_base64, redactar_pii
from proyecto.security.file_access import authorize_file_access

# Nombres de las tools MCP que expone el orquestador.
_TOOL_A_AGENTE = {
    'evaluar_expediente_micuenta': 'document_intelligence',
    "mcp_inspeccionar_dataset": "data_steward",
    "mcp_validar_calidad_datos": "data_steward",
    "mcp_evaluar_calidad": "data_steward",
    "mcp_buscar_normatividad": "normatividad_rag",
    "mcp_analizar_datos": "data_analyst",
    "mcp_listar_tablas": "data_analyst",
    "mcp_graficar_consulta": "data_analyst",
    "mcp_extraer_texto_imagen": "document_intelligence",
    "mcp_describir_imagen": "document_intelligence",
    "mcp_convertir_documento_markdown": "document_intelligence",
    # Tools de bajo nivel usadas por el backend de Data Analyst y por el grafo.
    "listar_tablas": "data_analyst",
    "describir_tabla": "data_analyst",
    "valores_columna": "data_analyst",
    "validar_sql": "data_analyst",
    "ejecutar_consulta": "data_analyst",
    "graficar_consulta": "data_analyst",
    "catalogo_metricas": "data_analyst",
    "consultar_metrica": "data_analyst",
    "resolver_periodo": "data_analyst",
    "resolver_valor": "data_analyst",
    "verificar_cifras": "data_analyst",
    "inspeccionar_dataset": "data_steward",
    "validar_calidad_datos": "data_steward",
    "evaluar_calidad": "data_steward",
    "buscar_evidencia": "normatividad_rag",
    "verificar_cita": "normatividad_rag",
    "buscar_normatividad": "normatividad_rag",
    "ocr_image": "document_intelligence",
    "describe_an_image": "document_intelligence",
    "document_to_markdown": "document_intelligence",
    "convertir_documento_markdown": "document_intelligence",
}


class Paso(TypedDict, total=False):
    agente: str
    accion: str
    detalle: str
    duracion: float | None
    estado: str  # "ok" | "alerta" | "error"


class Respuesta(TypedDict, total=False):
    respuesta: str
    pasos: list[Paso]
    agentes: list[str]
    herramientas: list[str]
    alertas: list[str]
    sql: str | None
    datos: list[dict] | None
    grafica: bytes | None
    fuentes: list[str]
    resultados: list[dict]
    interpretacion: dict
    aclaracion: dict
    supuestos: list[str]
    duracion: float | None
    modelo: str | None
    nota: str | None
    error: str | None
    formato: str | None
    validacion_documental: dict
    integridad_documental: dict
    pertinencia_documental: dict
    finalidad_documental: dict
    metadatos_documentales: dict
    evidencia_documental: list[dict]
    code: str


def _token_actual() -> str:
    """Token de la sesión activa, venga de Streamlit o del entorno."""
    token = os.environ.get("PROYECTO_AUTH_TOKEN", "")
    try:
        import streamlit as st

        token = str(st.session_state.get("auth_token", token) or token)
    except Exception:  # noqa: BLE001 - el servicio también se usa sin Streamlit
        pass
    return token.strip()


def _llamar_mcp_document_intelligence(
    nombre_tool: str,
    ruta: str,
    campo: str = "image_path",
    extra: dict[str, str] | None = None,
) -> str:
    """Llama una tool del servidor MCP por stdio."""
    if campo == "file_path":
        # Misma propagacion de entorno privado que preparacion y preguntas.
        from proyecto.mcp_servers.mcp_client import llamar_herramienta
        return llamar_herramienta(
            "proyecto.mcp_servers.document_intelligence_server", nombre_tool, {campo: ruta, **(extra or {})},
        )
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from proyecto.security.local_auth import subprocess_env

    # El subproceso MCP debe recibir la misma identidad que la UI para que
    # document_intelligence aplique el aislamiento de archivos.
    entorno = subprocess_env()
    token = _token_actual()
    if token:
        entorno["PROYECTO_AUTH_TOKEN"] = token

    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", "proyecto.mcp_servers.document_intelligence_server"],
        cwd=str(_RAIZ),
        env=entorno,
    )
    argumentos = {campo: ruta, **(extra or {})}

    async def ejecutar() -> str:
        timeout = float(os.getenv("MCP_DOCUMENT_TIMEOUT", "1800"))
        async with Client(transporte, timeout=timeout) as cliente:
            resultado = await cliente.call_tool(nombre_tool, argumentos)
            if resultado.is_error:
                raise RuntimeError(f"El servidor MCP falló al ejecutar {nombre_tool}.")
            return "\n".join(
                bloque.text
                for bloque in resultado.content
                if bloque.type == "text"
            ).strip()

    return asyncio.run(ejecutar())


_PAGINAS_RE = re.compile(
    r"\b(?:p[aá]ginas?|pags?\.?|hojas?)\s+(\d+(?:\s*(?:-|a|y|,)\s*\d+)*)",
    re.IGNORECASE,
)


def _paginas_solicitadas(consulta: str) -> str:
    """Extrae un rango de páginas de la consulta, por ejemplo ``1-5,8``."""
    coincidencia = _PAGINAS_RE.search(consulta or "")
    if not coincidencia:
        return ""
    fragmento = coincidencia.group(1)
    fragmento = re.sub(r"\s*a\s*", "-", fragmento, flags=re.IGNORECASE)
    fragmento = re.sub(r"\s*y\s*", ",", fragmento, flags=re.IGNORECASE)
    fragmento = re.sub(r"\s*-\s*", "-", fragmento)
    fragmento = re.sub(r"\s*,\s*", ",", fragmento)
    return fragmento.strip(" ,-")


def _mensaje_formato(sufijo: str, permitidas: set[str]) -> str:
    """Mensaje público y estable para un formato de archivo no soportado."""
    return (
        f"Formato no soportado: {sufijo or 'sin extensión'}. "
        f"Usa: {', '.join(sorted(permitidas))}."
    )


def _respuesta_error_archivo(
    mensaje: str,
    *,
    detalle: str,
    accion: str,
    duracion: float,
    fuente: str,
) -> Respuesta:
    """Respuesta de error común para archivos rechazados por el servicio."""
    return {
        "respuesta": mensaje,
        "pasos": [
            {
                "agente": "document_intelligence",
                "accion": accion,
                "detalle": detalle,
                "duracion": duracion,
                "estado": "error",
            }
        ],
        "agentes": ["document_intelligence"],
        "herramientas": [],
        "fuentes": [fuente],
        "duracion": duracion,
        "error": mensaje,
        "code": "ERROR_TECNICO",
        "formato": "markdown",
    }


def procesar_imagen(ruta: str, operacion: str = "ocr") -> Respuesta:
    """Procesa una imagen local mediante el servidor MCP, con respaldo local."""
    inicio = time.perf_counter()
    ruta_path = Path(ruta)
    es_descripcion = operacion == "descripcion"
    etiqueta = "Descripción" if es_descripcion else "OCR"
    nombre_tool = "describir_imagen" if es_descripcion else "extraer_texto_imagen"
    herramienta_local = describe_an_image if es_descripcion else ocr_image

    try:
        ruta_path = authorize_file_access(ruta, token=_token_actual())
    except PermissionError as exc:
        mensaje = f"Acceso denegado: {exc}"
        return _respuesta_error_archivo(
            mensaje,
            detalle=ruta_path.name,
            accion=f"{etiqueta} de imagen",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=ruta_path.name,
        )

    if not ruta_path.is_file():
        return _respuesta_error_archivo(
            f"No existe la imagen: {ruta}",
            detalle=ruta_path.name,
            accion=f"{etiqueta} de imagen",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=ruta_path.name,
        )

    sufijo = ruta_path.suffix.lower()
    if sufijo not in EXTENSIONES_IMAGEN:
        return _respuesta_error_archivo(
            _mensaje_formato(sufijo, EXTENSIONES_IMAGEN),
            detalle=ruta_path.name,
            accion=f"{etiqueta} de imagen",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=ruta_path.name,
        )

    if ruta_path.stat().st_size > MAX_IMAGE_BYTES:
        return _respuesta_error_archivo(
            "La imagen supera el límite de 10 MB.",
            detalle=ruta_path.name,
            accion=f"{etiqueta} de imagen",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=ruta_path.name,
        )

    via = "MCP"
    error: str | None = None
    try:
        texto = _llamar_mcp_document_intelligence(nombre_tool, str(ruta_path))
    except Exception as exc:  # noqa: BLE001 - se intenta el respaldo local
        try:
            token = _token_actual()
            if token:
                from proyecto.security.local_auth import authenticated

                with authenticated(token):
                    texto = herramienta_local.invoke({"image_path": str(ruta_path)})
            else:
                texto = herramienta_local.invoke({"image_path": str(ruta_path)})
            # El servidor MCP ya filtra por relevancia; el respaldo local
            # debe aplicar el mismo filtro.
            texto = filtrar_documento(texto, ruta_path.name, modo="rules")
            via = f"Fallback local ({type(exc).__name__})"
        except Exception as exc_local:  # noqa: BLE001
            texto = (
                "No se pudo procesar la imagen: "
                f"{type(exc_local).__name__}: {exc_local}"
            )
            via = "Error"
            error = texto

    if texto.startswith("No se pudo procesar la imagen:"):
        error = texto
    rechazado = es_rechazo(texto)

    duracion = round(time.perf_counter() - inicio, 2)
    return {
        "respuesta": texto,
        "pasos": [
            {
                "agente": "document_intelligence",
                "accion": f"{etiqueta} de imagen",
                "detalle": f"{ruta_path.name} · vía {via}",
                "duracion": duracion,
                "estado": "error" if error else ("alerta" if rechazado else "ok"),
            }
        ],
        "agentes": ["document_intelligence"],
        "herramientas": [nombre_tool],
        "fuentes": [ruta_path.name],
        "duracion": duracion,
        "modelo": MODELO_VISION_POR_DEFECTO,
        "nota": f"Vía {via}",
        "error": error,
        "formato": "texto" if not es_descripcion and not error else "markdown",
    }


def procesar_documento(
    ruta: str,
    consulta: str = "",
    nombre: str | None = None,
) -> Respuesta:
    """Mantiene la identidad existente durante conversion, MCP y respaldo local."""
    from proyecto.security.local_auth import authenticated
    from proyecto.security.document_access import authorize_file
    try:
        _, token = _identidad_documental(consulta)
        with authenticated(token):
            authorize_file(ruta)
            return _procesar_documento_autorizado(ruta, consulta, nombre)
    except PermissionError:
        return {"respuesta": "Acceso denegado al documento solicitado.", "code": "ACCESO_DENEGADO", "error": "autorizacion_denegada", "pasos": [], "herramientas": []}


def _procesar_documento_autorizado(
    ruta: str, consulta: str = "", nombre: str | None = None,
) -> Respuesta:
    """Convierte un documento y entrega el resultado al orquestador."""
    inicio = time.perf_counter()
    try:
        _identidad_documental(consulta)
    except PermissionError:
        return {"respuesta": "Acceso denegado: se requiere una sesión autorizada para consultar documentos.", "error": "autorizacion_denegada", "code": "ACCESO_DENEGADO", "pasos": [], "herramientas": []}
    import streamlit as st
    st.session_state.pop("documento_consultable", None)
    from proyecto.security.document_access import authorize_file
    identifier, entry = authorize_file(ruta)
    ruta_path = Path(entry['path'])
    etiqueta = (nombre or ruta_path.name).strip() or ruta_path.name
    sufijo = ruta_path.suffix.lower()

    mensaje_error: str | None = None
    try:
        ruta_path = authorize_file_access(ruta, token=_token_actual())
    except PermissionError as exc:
        mensaje_error = f"Acceso denegado: {exc}"
    else:
        if not ruta_path.is_file():
            mensaje_error = f"No existe el documento: {ruta}"
        elif sufijo not in EXTENSIONES_DOCUMENTO:
            mensaje_error = _mensaje_formato(sufijo, EXTENSIONES_DOCUMENTO)
        elif ruta_path.stat().st_size > MAX_DOCUMENT_BYTES:
            mensaje_error = "El documento supera el límite de 25 MB."

    if mensaje_error:
        return _respuesta_error_archivo(
            mensaje_error,
            detalle=etiqueta,
            accion="No se pudo convertir el documento",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=etiqueta,
        )

    via = "MCP"
    error: str | None = None
    alertas: list[str] = []
    extra: dict[str, str] = {"modo": "auto"}
    if sufijo == ".pdf":
        paginas = _paginas_solicitadas(consulta)
        if paginas:
            extra["paginas"] = paginas
    try:
        texto = _llamar_mcp_document_intelligence(
            "convertir_documento_markdown",
            identifier,
            campo="file_path",
            extra=extra,
        )
    except Exception as exc:  # noqa: BLE001 - se intenta el respaldo local
        authorize_file(identifier)  # nunca usar fallback tras revocacion/denegacion
        try:
            texto = document_to_markdown.invoke(
                {"file_path": identifier, **extra}
            )
            via = f"Fallback local ({type(exc).__name__})"
        except PermissionError:
            raise
        except Exception as exc_local:  # noqa: BLE001
            texto = (
                "No se pudo convertir el documento a Markdown: "
                f"{type(exc_local).__name__}: {exc_local}"
            )
            via = "Error"
            error = texto

    if texto.startswith("No se pudo convertir el documento a Markdown:"):
        error = texto
    elif texto.startswith("MarkItDown no extrajo texto"):
        error = texto

    duracion = round(time.perf_counter() - inicio, 2)
    estado = "error" if error else ("alerta" if alertas else "ok")
    paso_conversion: Paso = {
        "agente": "document_intelligence",
        "accion": "Conversión de documento a Markdown",
        "detalle": f"{etiqueta} · vía {via}",
        "duracion": duracion,
        "estado": estado,
    }
    herramientas = (
        ["mcp_convertir_documento_markdown"]
        if via == "MCP"
        else ["document_to_markdown"]
    )

    if error or alertas:
        return {
            "respuesta": texto,
            "pasos": [paso_conversion],
            "agentes": ["document_intelligence"],
            "herramientas": herramientas,
            "alertas": alertas,
            "fuentes": [etiqueta],
            "duracion": duracion,
            "modelo": None,
            "nota": f"Documento · MarkItDown/PDF OCR · vía {via}",
            "error": error,
            "code": "ERROR_TECNICO",
            "formato": "markdown",
        }

    # El upload no alcanza el orquestador ni su memoria hasta validar su tipo.
    # Sin fallback permisivo: un fallo tecnico no equivale a irrelevancia.
    from proyecto.mcp_servers.mcp_client import llamar_herramienta
    try:
        validacion = json.loads(llamar_herramienta(
            "proyecto.mcp_servers.document_intelligence_server",
            "validar_tipo_documental", {"documento_id": identifier},
        ))
        if validacion.get("decision") not in {"aceptado", "rechazado", "indeterminado", "error_tecnico"}:
            raise ValueError("Contrato de validacion documental invalido")
    except Exception:
        validacion = {"decision": "error_tecnico", "message": "No se pudo ejecutar la validación documental por MCP. Comprueba Ollama y las referencias locales."}
    herramientas.append("validar_tipo_documental")
    pasos = [paso_conversion, {
        "agente": "document_intelligence", "accion": "Validación semántica del tipo documental",
        "detalle": validacion.get("code", validacion["decision"]), "duracion": validacion.get("seconds"),
        "estado": "ok" if validacion["decision"] == "aceptado" else "error" if validacion["decision"] == "error_tecnico" else "alerta",
    }]
    if validacion["decision"] != "aceptado":
        from proyecto.agents.document_questions import admission_failure
        return {
            "code": validacion.get("code") or admission_failure(validacion)["code"],
            "respuesta": validacion["message"], "pasos": pasos,
            "agentes": ["document_intelligence"], "herramientas": herramientas,
            "fuentes": [etiqueta], "duracion": round(time.perf_counter() - inicio, 2),
            "error": validacion["message"] if validacion["decision"] == "error_tecnico" else None,
            "formato": "markdown", "validacion_documental": validacion,
        }
    try:
        owner, token = _identidad_documental(consulta)
        from proyecto.security.local_auth import authenticated
        with authenticated(token):
            documento = json.loads(llamar_herramienta(
                "proyecto.mcp_servers.document_intelligence_server",
                "preparar_documento_consultable", {"file_path": identifier, "texto": ""},
            ))
        import streamlit as st
        if documento.get("code"):
            return {"respuesta": documento["message"], "code": documento["code"], "pasos": pasos,
                    "herramientas": herramientas, "validacion_documental": validacion, "formato": "markdown"}
        st.session_state.documento_consultable = {"owner": owner, "index": documento, "previous": ""}
        st.session_state.documento_nuevo = True
        herramientas.append("preparar_documento_consultable")
    except Exception:
        return {"respuesta": "No se pudo preparar el contexto autorizado del documento. Comprueba la extracción, los permisos y Ollama.",
                "error": "contexto_documental_no_disponible", "code": "ERROR_TECNICO", "pasos": pasos, "herramientas": herramientas,
                "validacion_documental": validacion, "formato": "markdown"}
    resultado = _responder_con_archivo(
        consulta,
        str(ruta_path),
        etiqueta,
        "documento",
        "markdown",
        texto,
        pasos,
        herramientas,
    )
    resultado["validacion_documental"] = validacion
    from proyecto.security.document_access import authorize_context
    authorize_context(documento)  # no mostrar el informe tras revocacion durante la respuesta
    if resultado.get('code') == 'ACCESO_DENEGADO':
        return resultado
    from proyecto.agents.document_questions import integrity_summary
    resultado['integridad_documental'] = documento['integrity']
    resultado['metadatos_documentales'] = documento['metadata']
    resultado['respuesta'] = integrity_summary(documento['integrity']) + '\n\n' + resultado['respuesta']
    return resultado


def _usuario_actual() -> str:
    """El ambito de memoria es la sesion y permisos reales, nunca un fallback local."""
    from proyecto.security.local_auth import authenticated
    from proyecto.security.document_access import binding
    with authenticated(_token_actual()):
        return binding()


def _get_orquestador():
    import streamlit as st

    usuario = _usuario_actual()
    actual = st.session_state.get("orquestador_sesion")
    if actual is None or st.session_state.get("orquestador_usuario") != usuario:
        from proyecto.orchestration.orquestador import Orquestador

        st.session_state.orquestador_sesion = Orquestador(usuario=usuario)
        st.session_state.orquestador_usuario = usuario
    return st.session_state.orquestador_sesion


def reiniciar_orquestador() -> None:
    """Limpia el historial del orquestador compartido por la UI."""
    import streamlit as st
    st.session_state.pop("documento_consultable", None)
    st.session_state.pop("usar_documento", None)
    st.session_state.pop("documento_nuevo", None)
    if 'orquestador_sesion' in st.session_state:
        st.session_state.orquestador_sesion.reiniciar()


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _operacion_explicita(consulta: str) -> str | None:
    """Devuelve ocr/descripcion solo cuando la instrucción es inequívoca."""
    texto = _normalizar(consulta)
    pide_ocr = any(
        palabra in texto
        for palabra in (
            "ocr",
            "texto",
            "transcribe",
            "transcripcion",
            "extrae",
            "extraer",
            "leer",
            "lectura",
            "literal",
        )
    )
    pide_descripcion = any(
        palabra in texto
        for palabra in (
            "describe",
            "descripcion",
            "que ves",
            "resume",
            "resumen",
            "interpreta",
        )
    )
    if pide_ocr and not pide_descripcion:
        return "ocr"
    if pide_descripcion and not pide_ocr:
        return "descripcion"
    return None


def _resumen_args(args: dict) -> str:
    partes = []
    for clave, valor in (args or {}).items():
        if clave in {"image_path", "ruta", "path"}:
            valor = Path(str(valor)).name
        texto = str(valor).replace("\n", " ").strip()
        if len(texto) > 100:
            texto = texto[:100].rstrip() + "…"
        partes.append(f"{clave}={texto}")
    return ", ".join(partes)


def _fuentes_de_salida(salida: str) -> list[str]:
    fuentes = re.findall(r"(?im)^\s*FUENTE:\s*(.+?)\s*$", salida or "")
    fuentes.extend(re.findall(r"(?im)^\s*Fuente:\s*(.+?)\s*$", salida or ""))
    bloque = re.search(r"(?is)Fuentes consultadas:\s*(.+)$", salida or "")
    if bloque:
        fuentes.extend(
            re.findall(r"(?m)^\s*-\s*(.+?)\s*$", bloque.group(1))
        )
    fuentes.extend(re.findall(r'"archivo"\s*:\s*"([^"]+)"', salida or ""))
    return list(dict.fromkeys(fuente.strip() for fuente in fuentes if fuente.strip()))


def _sql_de_salida(salida: str) -> str | None:
    bloques = re.findall(r"```sql\s*(.*?)```", salida or "", flags=re.IGNORECASE | re.DOTALL)
    if bloques:
        return bloques[-1].strip()
    consulta = re.search(r"(?is)\b(select|with)\b.+(?:;|$)", salida or "")
    return consulta.group(0).strip() if consulta else None


def _datos_para_ui(datos) -> list[dict] | None:
    """Convierte el contrato columnas/filas en la tabla que espera Streamlit."""
    if isinstance(datos, list):
        filas = [fila for fila in datos if isinstance(fila, dict)]
        return filas[:50] or None
    if not isinstance(datos, dict):
        return None
    columnas = datos.get("columnas")
    filas = datos.get("filas")
    if not isinstance(columnas, list) or not isinstance(filas, list):
        return None
    tabla: list[dict] = []
    for fila in filas[:50]:
        if not isinstance(fila, (list, tuple)):
            continue
        tabla.append(dict(zip([str(columna) for columna in columnas], fila)))
    return tabla or None


def _grafica_de_salida(salida: str) -> tuple[bytes | None, dict, list[dict] | None]:
    """Decodifica la salida JSON de una tool de gráfica, sin confiar en su forma."""
    try:
        datos = json.loads(salida or "{}")
    except (TypeError, ValueError):
        return None, {}, None
    if not isinstance(datos, dict) or datos.get("error"):
        return None, {}, None
    try:
        png = base64.b64decode(str(datos.get("png_base64") or ""))
    except Exception:  # noqa: BLE001 - salida externa mal formada
        return None, {}, None
    if not png:
        return None, {}, None
    tabla = _datos_para_ui({
        "columnas": datos.get("columnas") or [],
        "filas": datos.get("filas") or [],
    })
    return png, dict(datos.get("especificacion") or {}), tabla


def _pasos_de_herramientas(herramientas: list[dict]) -> tuple[list[Paso], list[str], list[str], list[str]]:
    pasos: list[Paso] = []
    agentes: list[str] = []
    nombres: list[str] = []
    alertas: list[str] = []

    for herramienta in herramientas:
        nombre = str(herramienta.get("name") or "desconocida")
        agente = _TOOL_A_AGENTE.get(nombre, "orquestador")
        estado = str(herramienta.get("estado") or "ok")
        salida = str(herramienta.get("salida") or "")
        detalle = nombre
        argumentos = _resumen_args(herramienta.get("args") or {})
        if argumentos:
            detalle += f" · {argumentos}"
        if estado == "error":
            detalle += " · error"
            alertas.append(f"{nombre} falló: {salida[:240]}")

        if agente not in agentes:
            agentes.append(agente)
        nombres.append(nombre)
        pasos.append(
            {
                "agente": agente,
                "accion": "Herramienta MCP invocada",
                "detalle": detalle,
                "estado": estado,
            }
        )

    return pasos, agentes, nombres, alertas


def _registrar_documento_en_orquestador(
    orquestador,
    ruta: str,
    operacion: str,
    texto: str,
    nombre: str | None = None,
) -> None:
    try:
        orquestador.registrar_documento(ruta, operacion, texto, nombre)
    except Exception:  # noqa: BLE001 - la traza no debe romper la respuesta
        pass


def _identidad_documental(consulta: str = "") -> tuple[str, str]:
    """Valida la identidad existente y liga el contexto al token/alcance reales."""
    import streamlit as st
    from proyecto.security.local_auth import authenticated, authorize_request
    from proyecto.agents.document_questions import session_owner
    token = st.session_state.get("auth_token", os.getenv("PROYECTO_AUTH_TOKEN", ""))
    with authenticated(token):
        authorize_request(consulta)
        owner = session_owner()
    return owner, token


def _preguntar_documento(consulta: str) -> Respuesta:
    import streamlit as st
    from proyecto.security.local_auth import authenticated
    from proyecto.mcp_servers.mcp_client import llamar_herramienta
    inicio = time.perf_counter()
    try:
        owner, token = _identidad_documental(consulta)
        context = st.session_state.get("documento_consultable")
        if not context or context["owner"] != owner:
            st.session_state.pop("documento_consultable", None)
            raise PermissionError("El documento no pertenece a la sesión autorizada actual.")
        from proyecto.security.document_access import authorize_context
        with authenticated(token):
            authorize_context(context['index'])
        from proyecto.orchestration.grafo import interpretar_finalidad_documental
        finalidad = interpretar_finalidad_documental(consulta, context['previous'])
        if not consulta.strip() or consulta.strip() == "Analiza el documento adjunto y responde lo más útil.":
            return {"respuesta": "Documento aceptado. Puedes preguntar por su contenido sin volver a subirlo.", "pasos": [],
                    "fuentes": [context["index"]["name"]], "herramientas": [], "formato": "markdown"}
        with authenticated(token):
            response = json.loads(llamar_herramienta(
                "proyecto.mcp_servers.document_intelligence_server", "responder_pregunta_documental",
                {"pregunta": consulta, "documento": context["index"], "anterior": context["previous"]},
            ))
            authorize_context(context['index'])
            from proyecto.security.local_auth import authorize_request
            authorize_request(consulta)
        if response["status"] in {"respondido", "evidencia_recuperada"}:
            context["previous"] = consulta
        text, _ = redactar_pii(response["message"])
        code = response.get("code", "OK")
        return {"respuesta": text, "pasos": [{"agente": "document_intelligence", "accion": "Recuperación del adjunto por embeddings",
                    "detalle": code, "estado": "error" if code in {"ERROR_TECNICO", "ACCESO_DENEGADO"} else "ok" if code == "OK" else "alerta"}],
                "herramientas": ["responder_pregunta_documental"], "agentes": ["document_intelligence"],
                "fuentes": [context["index"]["name"]], "evidencia_documental": response.get("evidence", []),
                "calculo_documental": response.get("calculation"), "modelo": response.get("model"),
                "integridad_documental": None if code == 'ACCESO_DENEGADO' else response.get('integrity', context['index'].get('integrity')),
                "finalidad_documental": finalidad,
                "pertinencia_documental": response.get('compatibility') if code != 'ACCESO_DENEGADO' else None,
                "metadatos_documentales": context['index'].get('metadata') if code != 'ACCESO_DENEGADO' else None,
                "code": code, "error": response["message"] if code in {"ERROR_TECNICO", "ACCESO_DENEGADO"} else None,
                "estado_documental": response["status"], "duracion": round(time.perf_counter() - inicio, 2), "formato": "markdown"}
    except PermissionError:
        return {"respuesta": "Acceso denegado: el documento o la consulta no pertenecen a tu alcance autorizado.",
                "error": "autorizacion_denegada", "code": "ACCESO_DENEGADO", "pasos": [], "herramientas": []}
    except Exception:
        return {"respuesta": "No se pudo consultar el contexto documental. Comprueba Ollama y vuelve a intentarlo; no se ha inferido una respuesta.",
                "error": "error_tecnico_documental", "code": "ERROR_TECNICO", "pasos": [], "herramientas": []}

def _respuesta_rechazo(
    consulta: str,
    ruta: str,
    etiqueta: str,
    texto: str,
    pasos_previos: list[Paso] | None = None,
    herramientas: list[str] | None = None,
) -> Respuesta:
    """Respuesta determinista para un archivo ajeno a banca o finanzas.

    No se llama al orquestador ni se guarda el archivo en el contexto
    documental: así su contenido no puede influir en la conversación.
    """
    # Las rutas de la UI son temporales (documento_<uuid>.pdf); el usuario
    # reconoce el nombre original.
    nombre_temporal = Path(ruta).name
    mensaje = texto.strip()
    if etiqueta and nombre_temporal and nombre_temporal != etiqueta:
        mensaje = mensaje.replace(f"«{nombre_temporal}»", f"«{etiqueta}»")

    try:
        _get_orquestador().registrar_documento_rechazado(ruta, mensaje, consulta)
    except Exception:  # noqa: BLE001 - el rechazo no debe romper la respuesta
        pass

    pasos: list[Paso] = [
        *(pasos_previos or []),
        {
            "agente": "document_intelligence",
            "accion": "Archivo rechazado por relevancia",
            "detalle": f"{etiqueta} · no relacionado con banca o finanzas",
            "estado": "alerta",
        },
    ]
    return {
        "respuesta": mensaje,
        "pasos": pasos,
        "agentes": ["document_intelligence"],
        "herramientas": list(dict.fromkeys(herramientas or [])),
        "alertas": [
            f"{etiqueta}: archivo fuera del alcance bancario; no se guardó "
            "ni se usó en la conversación."
        ],
        "fuentes": [],
        "nota": "Filtro de relevancia documental",
        "formato": "markdown",
    }


def _responder_con_archivo(
    consulta: str,
    ruta: str,
    etiqueta: str,
    tipo: str,
    operacion: str,
    texto: str,
    pasos_previos: list[Paso],
    herramientas: list[str],
) -> Respuesta:
    """Entrega el resultado del subagente al orquestador para la respuesta final."""
    if tipo == "documento":
        from proyecto.agents.document_questions import external_request
        resultado = preguntar_orquestador(consulta) if external_request(consulta) else _preguntar_documento(consulta)
        resultado["pasos"] = pasos_previos + resultado.get("pasos", [])
        resultado["herramientas"] = herramientas + resultado.get("herramientas", [])
        return resultado
    if es_rechazo(texto):
        return _respuesta_rechazo(
            consulta, ruta, etiqueta, texto, pasos_previos, herramientas
        )

    try:
        _registrar_documento_en_orquestador(
            _get_orquestador(),
            ruta,
            operacion,
            texto,
            etiqueta,
        )
    except Exception:  # noqa: BLE001 - el subagente ya procesó el archivo
        pass

    instruccion = (consulta or "").strip()
    if not instruccion:
        instruccion = "Analiza el archivo adjunto y responde lo más útil."

    prompt = (
        f"El subagente document_intelligence ya procesó el {tipo} adjunto. "
        f"Instrucción del cliente: {instruccion}\n\n"
        "Usa el resultado procesado como evidencia y responde al cliente. "
        "Decide qué hacer con la instrucción: si pide OCR, transcripción, "
        "descripción o Markdown, entrégalo; si no, sintetiza la respuesta y "
        "evita repetir todo el contenido. No inventes datos."
    )
    resultado = preguntar_orquestador(prompt)

    if resultado.get("error"):
        resultado["respuesta"] = (
            "No pude generar la respuesta final con el orquestador. "
            f"{resultado.get('respuesta') or ''}"
        ).strip()

    pasos = [
        *pasos_previos,
        *(resultado.get("pasos") or []),
    ]
    agentes: list[str] = []
    for agente in [
        "document_intelligence",
        "orquestador",
        *(resultado.get("agentes") or []),
    ]:
        if agente and agente not in agentes:
            agentes.append(agente)

    nombres: list[str] = []
    for herramienta in [
        *herramientas,
        *(resultado.get("herramientas") or []),
    ]:
        if herramienta and herramienta not in nombres:
            nombres.append(herramienta)

    resultado["pasos"] = pasos
    resultado["agentes"] = agentes
    resultado["herramientas"] = nombres
    resultado["fuentes"] = [etiqueta]
    resultado["nota"] = "Subagente document_intelligence · respuesta del orquestador"
    resultado["formato"] = "markdown"
    return resultado


def procesar_adjuntos_chat(archivos: list[tuple[str, str, str]], consulta: str) -> Respuesta:
    """Mismo chat: registra referencias temporales y deja decidir al orquestador."""
    from proyecto.security.local_auth import authenticated
    from proyecto.orchestration.grafo import intencion_micuenta
    try:
        _, token = _identidad_documental(consulta)
        with authenticated(token):
            orquestador = _get_orquestador()
            orquestador.registrar_adjuntos_chat([ref for ref, _, _ in archivos])
        if len(archivos) == 1 and not intencion_micuenta(consulta):
            ref, nombre, _ = archivos[0]
            return procesar_documento(ref, consulta, nombre)
        return preguntar_orquestador(consulta)
    except PermissionError:
        return {'respuesta': 'Acceso denegado a los documentos de esta sesión.', 'code': 'ACCESO_DENEGADO', 'herramientas': []}
    except ValueError:
        return {'respuesta': 'Adjunta como máximo ocho PDF autorizados en esta conversación.', 'code': 'ERROR_TECNICO', 'herramientas': []}


def preguntar_orquestador(
    consulta: str, perfil: dict | None = None
) -> Respuesta:
    """Atiende una consulta de texto con el orquestador y sus tools MCP.

    ``perfil`` describe la sesión autenticada (rol y cliente_id). Es opcional
    para no romper la UI ni los llamados existentes.
    """
    inicio = time.perf_counter()
    try:
        import streamlit as st
        from proyecto.agents.document_questions import external_request
        from proyecto.orchestration.grafo import intencion_micuenta
        current = st.session_state.get('orquestador_sesion')
        intent = intencion_micuenta(consulta, seguimiento=getattr(current, '_expediente_en_curso', False))
        attached = getattr(current, '_adjuntos_chat', {})
        if attached and not intent and not external_request(consulta) and re.search(
                r'document|archivo|adjunt|\bpdf\b|saldo|cargos|movimientos|vigencia|curp', consulta, re.I):
            from proyecto.security.local_auth import authenticated
            from proyecto.security.document_access import authorize_file
            _, token = _identidad_documental(consulta)
            with authenticated(token):
                for ref in attached:
                    authorize_file(ref)
            selected = [ref for ref, name in attached.items() if name.casefold() in consulta.casefold()]
            if len(selected) == 1:
                return procesar_documento(selected[0], consulta, attached[selected[0]])
            if len(attached) > 1:
                return {'respuesta': 'Hay varios PDF en esta conversación. Indica el nombre del documento que deseas consultar, o pide evaluar el expediente MiCuenta completo.',
                        'code': 'ACLARACION_NECESARIA', 'herramientas': []}
        if (st.session_state.get("documento_consultable")
                and not intent
                and st.session_state.get("usar_documento", True)
                and not external_request(consulta)):
            return _preguntar_documento(consulta)
        orquestador = _get_orquestador()

        # Si la imagen se adjuntó antes y el usuario responde con una
        # instrucción clara, procesarla directo evita que el LLM resuma el OCR
        # o desvíe la pregunta a data_analyst.
        pendiente = getattr(orquestador, "imagen_pendiente", None)
        operacion = _operacion_explicita(consulta)
        if pendiente and operacion:
            resultado = procesar_imagen(pendiente, operacion)
            if resultado.get("error"):
                return resultado
            return _responder_con_archivo(
                consulta,
                pendiente,
                Path(pendiente).name,
                "imagen",
                operacion,
                str(resultado.get("respuesta") or ""),
                resultado.get("pasos") or [],
                resultado.get("herramientas") or [],
            )

        token = _token_actual()

        if token:
            from langsmith import tracing_context
            from proyecto.security.local_auth import authenticated, authorize_request

            with authenticated(token), tracing_context(enabled=False):
                authorize_request(consulta)
                texto = orquestador.procesar(consulta, perfil=perfil)
        else:
            from proyecto.security.local_auth import authorize_request
            authorize_request(consulta)
            texto = orquestador.procesar(consulta, perfil=perfil)
    except PermissionError:
        # El control de alcance es determinista y ocurre antes del LLM. Una
        # negativa esperada no es un fallo técnico: se responde como rechazo
        # controlado para no mostrar excepciones ni sugerir que se reintente.
        duracion = round(time.perf_counter() - inicio, 2)
        mensaje = (
            "No puedo proporcionar esa información: la solicitud está fuera "
            "del alcance autorizado para esta sesión."
        )
        return {
            "respuesta": mensaje,
            "pasos": [
                {
                    "agente": "orquestador",
                    "accion": "Solicitud rechazada",
                    "detalle": "Fuera del alcance autorizado",
                    "duracion": duracion,
                    "estado": "alerta",
                }
            ],
            "agentes": ["orquestador"],
            "herramientas": [],
            "duracion": duracion,
            "error": None,
            "formato": "markdown",
        }
    except Exception as exc:  # noqa: BLE001 - se informa en la UI
        duracion = round(time.perf_counter() - inicio, 2)
        mensaje = (
            "No pude completar la consulta con el orquestador: "
            f"{type(exc).__name__}: {exc}"
        )
        return {
            "respuesta": mensaje,
            "pasos": [
                {
                    "agente": "orquestador",
                    "accion": "Falló la consulta",
                    "detalle": type(exc).__name__,
                    "duracion": duracion,
                    "estado": "error",
                }
            ],
            "agentes": ["orquestador"],
            "herramientas": [],
            "duracion": duracion,
            "error": mensaje,
            "formato": "markdown",
        }

    duracion = round(time.perf_counter() - inicio, 2)
    herramientas = list(getattr(orquestador, "ultimas_herramientas", []) or [])
    invocadas = list(getattr(orquestador, "agentes_invocados", []) or [])
    pasos, agentes, nombres, alertas = _pasos_de_herramientas(herramientas)
    estado_grafo = getattr(orquestador, "ultimo_estado_grafo", {}) or {}
    interpretacion = dict(estado_grafo.get("interpretacion") or {})
    aclaracion = dict(estado_grafo.get("aclaracion") or {})
    supuestos = [
        str(supuesto).strip()
        for supuesto in estado_grafo.get("supuestos") or []
        if str(supuesto).strip()
    ]

    if aclaracion.get("necesaria"):
        pasos.insert(
            0,
            {
                "agente": "orquestador",
                "accion": "Aclaración necesaria",
                "detalle": str(aclaracion.get("pregunta") or ""),
                "duracion": duracion,
                "estado": "alerta",
            },
        )
        agentes = ["orquestador", *agentes]
    elif supuestos:
        pasos.insert(
            0,
            {
                "agente": "orquestador",
                "accion": "Interpretación",
                "detalle": " ".join(supuestos),
                "duracion": duracion,
                "estado": "ok",
            },
        )
        agentes = ["orquestador", *agentes]

    if not pasos:
        pasos = [
            {
                "agente": "orquestador",
                "accion": "Respondió directamente",
                "detalle": "Sin herramientas MCP",
                "duracion": duracion,
            }
        ]
        agentes = ["orquestador"]

    fuentes: list[str] = []
    sql: str | None = None
    grafica: bytes | None = None
    datos: list[dict] | None = None
    for herramienta in herramientas:
        salida = str(herramienta.get("salida") or "")
        for fuente in _fuentes_de_salida(salida):
            if fuente not in fuentes:
                fuentes.append(fuente)
        nombre_tool = herramienta.get("name")
        if nombre_tool == "mcp_analizar_datos":
            sql = _sql_de_salida(salida) or sql
        elif nombre_tool == "mcp_graficar_consulta":
            argumentos_sql = herramienta.get("args") or {}
            if isinstance(argumentos_sql, dict):
                sql = str(argumentos_sql.get("sql") or sql or "") or None
        if nombre_tool in {"mcp_graficar_consulta", "graficar_consulta"}:
            png, _especificacion, tabla = _grafica_de_salida(salida)
            if png is not None:
                grafica = png
                datos = tabla or datos
        argumentos = herramienta.get("args") or {}
        ruta = argumentos.get("image_path") if isinstance(argumentos, dict) else None
        if ruta:
            nombre = Path(str(ruta)).name
            if nombre not in fuentes:
                fuentes.append(nombre)

    # El grafo deja el resultado estructurado del analista en el estado. Es la
    # fuente más fiable para SQL, tabla y PNG.
    resultado_analista = (estado_grafo.get("resultados") or {}).get(
        "data_analyst", {}
    )
    if isinstance(resultado_analista, dict):
        sql = resultado_analista.get("sql") or sql
        if isinstance(resultado_analista.get("grafica"), bytes):
            grafica = resultado_analista["grafica"]
        datos = _datos_para_ui(resultado_analista.get("datos")) or datos
        if resultado_analista.get("grafica_error") and not any(
            "mcp_graficar_consulta" in alerta
            for alerta in alertas
        ):
            alertas.append(
                "No se pudo generar la gráfica: "
                f"{resultado_analista['grafica_error']}"
            )

    texto = limpiar_base64((texto or "").strip())
    if not texto:
        texto = "El orquestador no devolvió una respuesta."

    if isinstance(resultado_analista, dict) and resultado_analista:
        # La redacción por defecto es total. Solo una sesión de auditoría con
        # un permiso explícito (pii:rfc, pii:nombre) puede conservar ese campo.
        permitidos_pii: set[str] = set()
        try:
            from proyecto.security.local_auth import principal

            permisos = set(principal().get("permissions") or [])
            if "audit" in permisos:
                permitidos_pii = {
                    permiso.split(":", 1)[1]
                    for permiso in permisos
                    if permiso.startswith("pii:")
                }
        except Exception:  # noqa: BLE001 - sin sesión se redacta todo
            permitidos_pii = set()
        texto, pii = redactar_pii(texto, permitidos=permitidos_pii)
        if pii:
            alertas.append(
                "Se ocultaron datos personales en la respuesta: "
                + ", ".join(sorted(set(pii)))
            )
        no_verificadas = (
            (resultado_analista.get("evidencia") or {}).get(
                "cifras_no_verificadas"
            )
            or []
        )
        if no_verificadas:
            alertas.append(
                "Cifras no encontradas en la evidencia: "
                + ", ".join(str(valor) for valor in no_verificadas[:5])
            )

    modo = getattr(orquestador, "modo_efectivo", None)
    nota = (
        f"Orquestador {modo} · LangSmith"
        if modo
        else "Orquestador MCP · LangSmith"
    )
    return {
        "respuesta": texto,
        "pasos": pasos,
        "agentes": agentes,
        "herramientas": nombres or invocadas,
        "fuentes": fuentes,
        "alertas": alertas,
        "sql": sql,
        "datos": datos,
        "grafica": grafica,
        "resultados": herramientas,
        "interpretacion": interpretacion,
        "aclaracion": aclaracion,
        "supuestos": supuestos,
        "duracion": duracion,
        "modelo": getattr(orquestador, "modelo", None),
        "nota": nota,
        "formato": "markdown",
    }


_CONSULTAS_IMAGEN_GENERICAS = {
    "",
    "analiza la imagen",
    "analiza la imagen adjunta",
    "procesa la imagen",
    "procesa la imagen adjunta",
}


def _preguntar_operacion_imagen(ruta_path: Path, nombre: str) -> Respuesta:
    """Pregunta en el chat si se quiere OCR o descripción, sin llamar a Qwen."""
    inicio = time.perf_counter()
    pregunta = "¿Quieres que extraiga el texto (OCR) o que describa la imagen?"
    try:
        orquestador = _get_orquestador()
        orquestador.registrar_imagen(str(ruta_path), pregunta)
    except Exception as exc:  # noqa: BLE001 - se informa en la UI
        duracion = round(time.perf_counter() - inicio, 2)
        mensaje = (
            "Recibí la imagen, pero no pude preparar el contexto del "
            f"orquestador: {type(exc).__name__}: {exc}"
        )
        return {
            "respuesta": mensaje,
            "pasos": [
                {
                    "agente": "orquestador",
                    "accion": "No pudo registrar la imagen",
                    "detalle": type(exc).__name__,
                    "duracion": duracion,
                    "estado": "error",
                }
            ],
            "agentes": ["orquestador"],
            "herramientas": [],
            "duracion": duracion,
            "error": mensaje,
            "formato": "markdown",
        }

    duracion = round(time.perf_counter() - inicio, 2)
    return {
        "respuesta": pregunta,
        "pasos": [
            {
                "agente": "document_intelligence",
                "accion": "Imagen recibida",
                "detalle": nombre,
                "duracion": duracion,
                "estado": "ok",
            }
        ],
        "agentes": ["document_intelligence"],
        "herramientas": [],
        "fuentes": [nombre],
        "duracion": duracion,
        "modelo": getattr(orquestador, "modelo", None),
        "nota": "Imagen lista · esperando instrucción",
        "formato": "markdown",
    }


def procesar_imagen_auto(
    ruta: str,
    consulta: str = "",
    nombre: str | None = None,
) -> Respuesta:
    """Pregunta OCR/descripción o deja que el orquestador decida."""
    inicio = time.perf_counter()
    ruta_path = Path(ruta)
    etiqueta = (nombre or ruta_path.name).strip() or ruta_path.name

    try:
        ruta_path = authorize_file_access(ruta, token=_token_actual())
    except PermissionError as exc:
        mensaje = f"Acceso denegado: {exc}"
        return _respuesta_error_archivo(
            mensaje,
            detalle=etiqueta,
            accion="Imagen rechazada",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=etiqueta,
        )

    if not ruta_path.is_file():
        return procesar_imagen(str(ruta_path), "ocr")

    sufijo = ruta_path.suffix.lower()
    if sufijo not in EXTENSIONES_IMAGEN:
        return _respuesta_error_archivo(
            _mensaje_formato(sufijo, EXTENSIONES_IMAGEN),
            detalle=etiqueta,
            accion="Imagen rechazada",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=etiqueta,
        )

    if ruta_path.stat().st_size > MAX_IMAGE_BYTES:
        return _respuesta_error_archivo(
            "La imagen supera el límite de 10 MB.",
            detalle=etiqueta,
            accion="Imagen rechazada",
            duracion=round(time.perf_counter() - inicio, 2),
            fuente=etiqueta,
        )

    indicacion = (consulta or "").strip().rstrip(" .!?")
    if indicacion.casefold() in _CONSULTAS_IMAGEN_GENERICAS:
        return _preguntar_operacion_imagen(ruta_path, etiqueta)

    # Instrucciones inequívocas ("extrae el texto", "descríbela") se resuelven
    # directo por MCP. Así el chat recibe el OCR completo y no un resumen del
    # LLM, y el texto queda disponible para la siguiente consulta.
    operacion = _operacion_explicita(indicacion)
    if operacion:
        resultado = procesar_imagen(str(ruta_path), operacion)
        if resultado.get("error"):
            resultado["fuentes"] = [etiqueta]
            resultado["nota"] = "Imagen · MCP directo"
            return resultado
        return _responder_con_archivo(
            indicacion,
            str(ruta_path),
            etiqueta,
            "imagen",
            operacion,
            str(resultado.get("respuesta") or ""),
            resultado.get("pasos") or [],
            resultado.get("herramientas") or [],
        )

    # Si la intención es ambigua, el orquestador decide. Dejamos la imagen
    # marcada como pendiente para que una respuesta posterior ("descríbela",
    # "extrae el texto") también pueda resolverse sin volver a adivinar.
    try:
        _get_orquestador().marcar_imagen_pendiente(str(ruta_path))
    except Exception:  # noqa: BLE001
        pass

    prompt = (
        f"{indicacion}\n\n"
        f"Adjunté una imagen en {ruta_path}. Decide si necesitas extraer el "
        "texto (OCR) o describir la imagen. Si parece una factura, recibo o "
        "comprobante, prefiere OCR; en otro caso, descríbela. Usa la "
        "herramienta MCP adecuada. Si la herramienta responde que el archivo "
        "está fuera de alcance, comunícalo sin describir su contenido. Si la "
        "intención no es clara, pregúntame qué quiero hacer."
    )

    resultado = preguntar_orquestador(prompt)
    if resultado.get("error"):
        alterno = procesar_imagen(str(ruta_path), "ocr")
        if es_rechazo(alterno.get("respuesta")):
            return _respuesta_rechazo(
                indicacion,
                str(ruta_path),
                etiqueta,
                str(alterno.get("respuesta") or ""),
                alterno.get("pasos") or [],
                alterno.get("herramientas") or [],
            )
        if not alterno.get("error"):
            try:
                _registrar_documento_en_orquestador(
                    _get_orquestador(),
                    str(ruta_path),
                    "ocr",
                    str(alterno.get("respuesta") or ""),
                    etiqueta,
                )
            except Exception:  # noqa: BLE001
                pass
        alterno["pasos"] = [
            {
                "agente": "orquestador",
                "accion": "Falló el orquestador",
                "detalle": "Se usó el servidor MCP directo para OCR",
                "estado": "alerta",
            },
            *alterno.get("pasos", []),
        ]
        alterno["fuentes"] = [etiqueta]
        alterno["nota"] = "MCP directo (fallback)"
        return alterno

    # Si el filtro de relevancia rechazó la imagen, la respuesta es el
    # rechazo y no se guarda nada en el contexto documental.
    for herramienta in resultado.get("resultados") or []:
        salida = str(herramienta.get("salida") or "")
        if es_rechazo(salida):
            return _respuesta_rechazo(
                indicacion,
                str(ruta_path),
                etiqueta,
                salida,
                resultado.get("pasos") or [],
                resultado.get("herramientas") or [],
            )

    # Guarda el resultado documental que haya usado el orquestador para que
    # las consultas de seguimiento no dependan de que el LLM recuerde el path.
    for herramienta in resultado.get("resultados") or []:
        nombre_tool = str(herramienta.get("name") or "")
        salida = str(herramienta.get("salida") or "")
        if herramienta.get("estado") == "error" or not salida:
            continue
        if nombre_tool == "mcp_extraer_texto_imagen":
            _registrar_documento_en_orquestador(
                _get_orquestador(),
                str(ruta_path),
                "ocr",
                salida,
                etiqueta,
            )
        elif nombre_tool == "mcp_describir_imagen":
            _registrar_documento_en_orquestador(
                _get_orquestador(),
                str(ruta_path),
                "descripcion",
                salida,
                etiqueta,
            )

    resultado["formato"] = "markdown"
    modelo_orquestador = resultado.get("modelo")
    resultado["modelo"] = (
        f"{modelo_orquestador} + {MODELO_VISION_POR_DEFECTO}"
        if modelo_orquestador
        else MODELO_VISION_POR_DEFECTO
    )
    resultado["fuentes"] = [etiqueta]
    resultado["nota"] = "Orquestador MCP · imagen"
    return resultado
