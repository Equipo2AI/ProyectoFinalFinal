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
    MAX_DOCUMENT_BYTES,
    MODELO_VISION_POR_DEFECTO,
    describe_an_image,
    document_to_markdown,
    ocr_image,
)
from proyecto.guardrails import limpiar_base64, redactar_pii

_ORQUESTADOR = None

# Nombres de las tools MCP que expone el orquestador.
_TOOL_A_AGENTE = {
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
    # Tools de bajo nivel usadas por el hub y por el grafo.
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
    duracion: float | None
    modelo: str | None
    nota: str | None
    error: str | None
    formato: str | None


def _llamar_mcp_document_intelligence(
    nombre_tool: str,
    ruta: str,
    campo: str = "image_path",
    extra: dict[str, str] | None = None,
) -> str:
    """Llama una tool del servidor MCP por stdio."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", "proyecto.mcp_servers.document_intelligence_server"],
        cwd=str(_RAIZ),
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


def procesar_imagen(ruta: str, operacion: str = "ocr") -> Respuesta:
    """Procesa una imagen local mediante el servidor MCP, con respaldo local."""
    inicio = time.perf_counter()
    ruta_path = Path(ruta).expanduser().resolve()
    es_descripcion = operacion == "descripcion"
    etiqueta = "Descripción" if es_descripcion else "OCR"
    nombre_tool = "describir_imagen" if es_descripcion else "extraer_texto_imagen"
    herramienta_local = describe_an_image if es_descripcion else ocr_image

    if not ruta_path.is_file():
        mensaje = f"No existe la imagen: {ruta}"
        return {
            "respuesta": mensaje,
            "pasos": [
                {
                    "agente": "document_intelligence",
                    "accion": f"{etiqueta} de imagen",
                    "detalle": mensaje,
                    "estado": "error",
                }
            ],
            "agentes": ["document_intelligence"],
            "herramientas": [],
            "error": mensaje,
            "formato": "markdown",
        }

    via = "MCP"
    error: str | None = None
    try:
        texto = _llamar_mcp_document_intelligence(nombre_tool, str(ruta_path))
    except Exception as exc:  # noqa: BLE001 - se intenta el respaldo local
        try:
            texto = herramienta_local.invoke({"image_path": str(ruta_path)})
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

    duracion = round(time.perf_counter() - inicio, 2)
    return {
        "respuesta": texto,
        "pasos": [
            {
                "agente": "document_intelligence",
                "accion": f"{etiqueta} de imagen",
                "detalle": f"{ruta_path.name} · vía {via}",
                "duracion": duracion,
                "estado": "error" if error else "ok",
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
    """Convierte un documento y entrega el resultado al orquestador."""
    inicio = time.perf_counter()
    ruta_path = Path(ruta).expanduser().resolve()
    etiqueta = (nombre or ruta_path.name).strip() or ruta_path.name
    sufijo = ruta_path.suffix.lower()

    mensaje_error: str | None = None
    if not ruta_path.is_file():
        mensaje_error = f"No existe el documento: {ruta}"
    elif sufijo not in EXTENSIONES_DOCUMENTO:
        permitidos = ", ".join(sorted(EXTENSIONES_DOCUMENTO))
        mensaje_error = (
            f"Formato no soportado: {sufijo or 'sin extensión'}. "
            f"Usa: {permitidos}."
        )
    elif ruta_path.stat().st_size > MAX_DOCUMENT_BYTES:
        mensaje_error = "El documento supera el límite de 25 MB."

    if mensaje_error:
        duracion = round(time.perf_counter() - inicio, 2)
        return {
            "respuesta": mensaje_error,
            "pasos": [
                {
                    "agente": "document_intelligence",
                    "accion": "No se pudo convertir el documento",
                    "detalle": etiqueta,
                    "duracion": duracion,
                    "estado": "error",
                }
            ],
            "agentes": ["document_intelligence"],
            "herramientas": [],
            "fuentes": [etiqueta],
            "duracion": duracion,
            "error": mensaje_error,
            "formato": "markdown",
        }

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
            str(ruta_path),
            campo="file_path",
            extra=extra,
        )
    except Exception as exc:  # noqa: BLE001 - se intenta el respaldo local
        try:
            texto = document_to_markdown.invoke(
                {"file_path": str(ruta_path), **extra}
            )
            via = f"Fallback local ({type(exc).__name__})"
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
        alertas.append(texto)

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
            "formato": "markdown",
        }

    return _responder_con_archivo(
        consulta,
        str(ruta_path),
        etiqueta,
        "documento",
        "markdown",
        texto,
        [paso_conversion],
        herramientas,
    )


def _usuario_actual() -> str:
    """Identidad autenticada para aislar el contexto documental."""
    token = os.environ.get("PROYECTO_AUTH_TOKEN", "")
    try:
        import streamlit as st

        token = str(st.session_state.get("auth_token", token) or token)
    except Exception:  # noqa: BLE001 - el servicio también se usa sin Streamlit
        pass

    if not token:
        return (
            os.getenv("PROYECTO_AUTH_SUBJECT", "local").strip() or "local"
        )
    try:
        from proyecto.security.local_auth import authenticated, principal

        with authenticated(token):
            return str(principal().get("subject") or "local").strip() or "local"
    except Exception:  # noqa: BLE001 - se usa un ámbito local seguro
        return os.getenv("PROYECTO_AUTH_SUBJECT", "local").strip() or "local"


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


def preguntar_orquestador(
    consulta: str, perfil: dict | None = None
) -> Respuesta:
    """Atiende una consulta de texto con el orquestador y sus tools MCP.

    ``perfil`` describe la sesión autenticada (rol y cliente_id). Es opcional
    para no romper la UI ni los llamados existentes.
    """
    inicio = time.perf_counter()
    try:
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

        token = os.environ.get("PROYECTO_AUTH_TOKEN", "")
        try:
            import streamlit as st

            token = st.session_state.get("auth_token", token) or token
        except Exception:  # noqa: BLE001 - el servicio también se usa sin Streamlit
            pass

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
    # fuente más fiable para SQL, tabla y PNG, sobre todo con el hub.
    estado_grafo = getattr(orquestador, "ultimo_estado_grafo", {}) or {}
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
        texto, pii = redactar_pii(texto)
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
    ruta_path = Path(ruta).expanduser().resolve()
    if not ruta_path.is_file():
        return procesar_imagen(str(ruta_path), "ocr")

    etiqueta = (nombre or ruta_path.name).strip() or ruta_path.name
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
        "comprobante, prefiere OCR; si es una foto general, descríbela. Usa la "
        "herramienta MCP adecuada. Si la intención no es clara, pregúntame qué "
        "quiero hacer."
    )

    resultado = preguntar_orquestador(prompt)
    if resultado.get("error"):
        alterno = procesar_imagen(str(ruta_path), "ocr")
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
