"""Contrato de respuesta entre la UI y el orquestador."""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path
from typing import TypedDict

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from proyecto.agents.document_intelligence_agent import (
    EXTENSIONES_IMAGEN,
    MAX_IMAGE_BYTES,
    MODELO_VISION_POR_DEFECTO,
    describe_an_image,
    ocr_image,
)

TIEMPO_DEMO = 1.1
NOTA_DEMO = "Datos sintéticos"

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


def _grafica_demo(datos: list[dict], tema: str = "oscuro") -> bytes:
    plt.rcParams.update(
        {
            "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"],
            "font.size": 8,
        }
    )
    productos = [str(fila.get("producto", "")) for fila in datos]
    saldos = [float(fila.get("saldo_promedio", 0)) for fila in datos]
    claro = tema == "claro"
    colores = (
        ["#C8102E", "#C98B4B", "#2F7F73", "#4C5B8A"]
        if claro
        else ["#C25256", "#B58A50", "#5B9A8F", "#7886A8"]
    )
    tinta = "#2A2622" if claro else "#E9DFD2"
    tenue = "#6A645C" if claro else "#B9AC9E"
    linea = "#171923" if claro else "#F4EDE3"
    fondo = "#FBF8F2" if claro else "#171A1D"

    figura, eje = plt.subplots(figsize=(7.0, 3.4), dpi=190)
    figura.patch.set_facecolor(fondo)
    eje.set_facecolor(fondo)
    barras = eje.bar(productos, saldos, color=colores, width=0.54, zorder=3)

    for barra, valor in zip(barras, saldos):
        eje.text(
            barra.get_x() + barra.get_width() / 2,
            valor + max(saldos) * 0.02,
            f"${valor:,.0f}",
            ha="center",
            va="bottom",
            color=tinta,
            fontsize=8,
        )

    eje.set_ylim(0, max(saldos) * 1.18)
    eje.yaxis.set_major_formatter(FuncFormatter(lambda valor, _: f"{valor:,.0f}"))
    eje.set_ylabel("Saldo promedio (MXN)", color=tenue, fontsize=8.5)
    eje.tick_params(colors=tenue, labelsize=8, length=0)
    eje.yaxis.grid(True, color=linea, alpha=0.08, linewidth=0.7)
    eje.set_axisbelow(True)
    for lado in ("top", "right", "left"):
        eje.spines[lado].set_visible(False)
    eje.spines["bottom"].set_color(linea)
    eje.spines["bottom"].set_alpha(0.14)
    eje.set_title(
        "Saldo promedio por producto · febrero 2026",
        color=tinta,
        fontsize=9.5,
        loc="left",
        pad=10,
    )

    figura.tight_layout(pad=0.4)
    bufer = io.BytesIO()
    figura.savefig(
        bufer,
        format="png",
        facecolor=fondo,
        bbox_inches="tight",
        pad_inches=0.08,
    )
    plt.close(figura)
    return bufer.getvalue()

def _analitica(tema: str = "oscuro") -> Respuesta:
    datos = [
        {"producto": "C_PERSONAL", "saldo_promedio": 45210.75},
        {"producto": "TDC", "saldo_promedio": 18760.40},
        {"producto": "AHORRO", "saldo_promedio": 12340.55},
        {"producto": "CHEQUES", "saldo_promedio": 8910.20},
    ]
    return {
        "respuesta": (
            "En febrero el saldo promedio más alto está en **C_PERSONAL** con "
            "$45,210.75, seguido de TDC con $18,760.40. AHORRO y CHEQUES se "
            "mantienen por debajo de $13,000."
        ),
        "pasos": [
            {
                "agente": "orquestador",
                "accion": "Clasificó la intención",
                "detalle": "Consulta analítica sobre saldos",
                "duracion": 0.2,
            },
            {
                "agente": "data_steward",
                "accion": "Validó metadatos y calidad",
                "detalle": "1 alerta de calidad",
                "duracion": 0.5,
                "estado": "alerta",
            },
            {
                "agente": "data_analyst",
                "accion": "Generó y ejecutó la consulta",
                "detalle": "4 productos · febrero 2026",
                "duracion": 1.4,
            },
        ],
        "alertas": ["25 saldos negativos detectados en raw_saldos."],
        "sql": (
            "SELECT p.descripcion AS producto,\n"
            "       ROUND(AVG(s.saldo_mes), 2) AS saldo_promedio\n"
            "FROM fact_saldos_captacoloca_m s\n"
            "JOIN dim_producto p USING (cve_producto)\n"
            "WHERE s.cve_fecha_informacion BETWEEN 20260201 AND 20260228\n"
            "GROUP BY p.descripcion\n"
            "ORDER BY saldo_promedio DESC;"
        ),
        "datos": datos,
        "grafica": _grafica_demo(datos, tema),
        "_grafica_demo": True,
        "fuentes": ["fact_saldos_captacoloca_m", "dim_producto"],
        "duracion": 2.1,
        "modelo": "llama3.1:latest",
        "nota": NOTA_DEMO,
    }


def _calidad() -> Respuesta:
    return {
        "respuesta": (
            "La tabla de saldos tiene **25 registros con saldo negativo** y "
            "**3 fechas fuera del rango** de información. No encontré nulos en "
            "las llaves, pero sí una diferencia entre el total de `raw_saldos` "
            "y `fact_saldos_captacoloca_m` que conviene revisar antes de "
            "publicar el cubo."
        ),
        "pasos": [
            {
                "agente": "orquestador",
                "accion": "Clasificó la intención",
                "detalle": "Gobierno y calidad de datos",
                "duracion": 0.2,
            },
            {
                "agente": "data_steward",
                "accion": "Inspeccionó la tabla de saldos",
                "detalle": "2 hallazgos · 25 registros afectados",
                "duracion": 0.9,
                "estado": "alerta",
            },
        ],
        "alertas": [
            "25 saldos negativos en raw_saldos: revisar si son sobregiros válidos o errores de captura.",
            "3 fechas de información fuera del periodo de febrero 2026.",
        ],
        "fuentes": ["raw_saldos", "fact_saldos_captacoloca_m", "catálogo de calidad"],
        "duracion": 1.1,
        "modelo": "llama3.1:latest",
        "nota": NOTA_DEMO,
    }


def _normativa() -> Respuesta:
    return {
        "respuesta": (
            "El glosario define **cliente activo** como la persona con al menos "
            "una cuenta o producto vigente y con actividad transaccional en los "
            "últimos 90 días. Un cliente con productos vigentes pero sin "
            "movimientos entra en la categoría de **cliente inactivo**."
        ),
        "pasos": [
            {
                "agente": "orquestador",
                "accion": "Clasificó la intención",
                "detalle": "Consulta normativa / glosario",
                "duracion": 0.2,
            },
            {
                "agente": "normatividad_rag",
                "accion": "Buscó en la memoria semántica",
                "detalle": "3 fragmentos recuperados",
                "duracion": 0.7,
            },
        ],
        "fuentes": [
            "Glosario de negocio · Cliente activo",
            "Términos y condiciones Citibanamex",
        ],
        "duracion": 0.9,
        "modelo": "gemma4:e4b",
        "nota": NOTA_DEMO,
    }


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
    ruta_path = Path(ruta).expanduser()
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
        "duracion": duracion,
        "modelo": getattr(orquestador, "modelo", None),
        "nota": "Orquestador MCP",
        "formato": "markdown",
    }


def preguntar(consulta: str, tema: str = "oscuro") -> Respuesta:
    time.sleep(TIEMPO_DEMO)
    texto = consulta.casefold()

    if any(clave in texto for clave in ("calidad", "nulo", "duplicado", "negativo")):
        return _calidad()
    if any(clave in texto for clave in ("glosario", "define", "definición", "cliente activo")):
        return _normativa()
    return _analitica(tema)


def regraficar(artefacto: dict, tema: str) -> dict:
    if not artefacto.get("_grafica_demo"):
        return artefacto
    datos = artefacto.get("datos") or []
    if datos and all("producto" in fila and "saldo_promedio" in fila for fila in datos):
        artefacto["grafica"] = _grafica_demo(datos, tema)
    return artefacto
