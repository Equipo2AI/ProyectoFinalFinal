"""Contrato de respuesta entre la UI y el orquestador.

La forma de ``Respuesta`` conserva la estructura que usaba la demo (pasos,
alertas, SQL, datos, gráfica y fuentes) como guía para las tools MCP que
devuelvan resultados estructurados. El flujo real ya no usa datos sintéticos.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import TypedDict

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from proyecto.agents.document_intelligence_agent import (
    EXTENSIONES_IMAGEN,
    MAX_IMAGE_BYTES,
    MODELO_VISION_POR_DEFECTO,
    describe_an_image,
    ocr_image,
)

_ORQUESTADOR = None

# Nombres de las tools MCP que expone el orquestador.
_TOOL_A_AGENTE = {
    "mcp_inspeccionar_dataset": "data_steward",
    "mcp_validar_calidad_datos": "data_steward",
    "mcp_buscar_normatividad": "normatividad_rag",
    "mcp_analizar_datos": "data_analyst",
    "mcp_extraer_texto_imagen": "document_intelligence",
    "mcp_describir_imagen": "document_intelligence",
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
    duracion: float | None
    modelo: str | None
    nota: str | None
    error: str | None
    formato: str | None


def _llamar_mcp_document_intelligence(nombre_tool: str, ruta: str) -> str:
    """Llama una tool del servidor MCP por stdio."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transporte = StdioTransport(
        command=sys.executable,
        args=["-m", "proyecto.mcp_servers.document_intelligence_server"],
        cwd=str(_RAIZ),
    )

    async def ejecutar() -> str:
        async with Client(transporte) as cliente:
            resultado = await cliente.call_tool(nombre_tool, {"image_path": ruta})
            if resultado.is_error:
                raise RuntimeError(f"El servidor MCP falló al ejecutar {nombre_tool}.")
            return "\n".join(
                bloque.text
                for bloque in resultado.content
                if bloque.type == "text"
            ).strip()

    return asyncio.run(ejecutar())


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


def _get_orquestador():
    global _ORQUESTADOR
    if _ORQUESTADOR is None:
        from proyecto.orchestration.orquestador import Orquestador

        _ORQUESTADOR = Orquestador()
    return _ORQUESTADOR


def reiniciar_orquestador() -> None:
    """Limpia el historial del orquestador compartido por la UI."""
    if _ORQUESTADOR is not None:
        _ORQUESTADOR.reiniciar()


def preguntar_orquestador(consulta: str) -> Respuesta:
    """Atiende una consulta de texto con el orquestador y sus tools MCP."""
    inicio = time.perf_counter()
    try:
        orquestador = _get_orquestador()
        texto = orquestador.procesar(consulta)
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
    invocadas = list(getattr(orquestador, "agentes_invocados", []))
    pasos: list[Paso] = []
    agentes: list[str] = []

    for herramienta in invocadas:
        agente = _TOOL_A_AGENTE.get(herramienta, "orquestador")
        if agente not in agentes:
            agentes.append(agente)
        pasos.append(
            {
                "agente": agente,
                "accion": "Herramienta MCP invocada",
                "detalle": herramienta,
            }
        )

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

    texto = (texto or "").strip()
    if not texto:
        texto = "El orquestador no devolvió una respuesta."

    return {
        "respuesta": texto,
        "pasos": pasos,
        "agentes": agentes,
        "herramientas": invocadas,
        "duracion": duracion,
        "modelo": getattr(orquestador, "modelo", None),
        "nota": "Orquestador MCP · LangSmith",
        "formato": "markdown",
    }


def procesar_imagen_orquestador(ruta: str, operacion: str = "ocr") -> Respuesta:
    """Pide al orquestador que use las tools MCP de imagen; si no, usa MCP directo."""
    ruta_path = Path(ruta).expanduser().resolve()
    if not ruta_path.is_file():
        return procesar_imagen(str(ruta_path), operacion)

    if operacion == "descripcion":
        consulta = (
            f"Usa la herramienta MCP de descripción de imágenes con "
            f"image_path='{ruta_path}'. Devuelve únicamente la descripción "
            "de la herramienta, sin introducciones ni comentarios."
        )
        esperada = "mcp_describir_imagen"
    else:
        consulta = (
            f"Usa la herramienta MCP de OCR con image_path='{ruta_path}'. "
            "Devuelve únicamente el texto transcrito de la imagen, sin "
            "resúmenes, introducciones ni comentarios."
        )
        esperada = "mcp_extraer_texto_imagen"

    resultado = preguntar_orquestador(consulta)
    herramientas = set(resultado.get("herramientas") or [])
    texto = (resultado.get("respuesta") or "").strip()
    error_imagen = (
        "No existe la imagen" in texto
        or "No se pudo procesar la imagen" in texto
    )

    if resultado.get("error") or esperada not in herramientas or not texto or error_imagen:
        alterno = procesar_imagen(str(ruta_path), operacion)
        alterno["pasos"] = [
            {
                "agente": "orquestador",
                "accion": "No invocó la tool de imagen",
                "detalle": "Se usó el servidor MCP directo",
                "estado": "alerta",
            },
            *alterno.get("pasos", []),
        ]
        alterno["nota"] = "MCP directo (fallback)"
        return alterno

    modelo_orquestador = resultado.get("modelo")
    resultado["modelo"] = (
        f"{modelo_orquestador} + {MODELO_VISION_POR_DEFECTO}"
        if modelo_orquestador
        else MODELO_VISION_POR_DEFECTO
    )
    resultado["fuentes"] = [ruta_path.name]
    resultado["nota"] = "Orquestador MCP · imagen"
    resultado["formato"] = "texto" if operacion == "ocr" else "markdown"
    return resultado
