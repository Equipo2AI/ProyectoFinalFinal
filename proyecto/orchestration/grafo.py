"""Orquestación explícita en grafo con LangGraph.

El grafo reemplaza el enrutamiento opaco del hub por un flujo con estado
compartido:

    planear -> despachar -> especialista -> despachar -> ... -> verificar
       ^                                                        |
       └──────────────────── reintento (máx. 1) ────────────────┘
                                                                |
                                                           sintetizar

Cada especialista lee el estado común y deja allí su resultado. El verificador
puede pedir un reintento de los especialistas que fallaron, y el sintetizador
compone la respuesta final sin inventar datos.

El modo se controla con ``ORCHESTRATION_MODE``:
- ``graph`` (por defecto): usa este grafo.
- ``hub``: usa el orquestador anterior como respaldo.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
import json
import operator
import os
import re
from pathlib import Path
from typing import Annotated, Any, TypedDict

from proyecto.config.ollama_runtime import ChatOllama
from langgraph.graph import END, START, StateGraph

from proyecto.agents import data_steward_agent
from proyecto.guardrails import limpiar_base64
from proyecto.agents.document_intelligence_agent import (
    PALABRAS_DESCRIPCION,
    PATRON_RUTA_DOCUMENTO,
    PATRON_RUTA_IMAGEN,
)
from proyecto.config.models import modelo_de
from proyecto.orchestration.hubs import HubDataAnalyst
from proyecto.orchestration.orquestador import (
    _es_consulta_normativa,
    _llamar_mcp,
    _normalizar,
)
from proyecto.orchestration.tareas import (
    ContextoHub,
    DominioTarea,
    EstadoResultado,
    ResultadoTarea,
    Tarea,
)

_ESPECIALISTAS = {
    "data_steward",
    "data_analyst",
    "normatividad_rag",
    "document_intelligence",
    "compliance",
}

_ETIQUETAS = {
    "data_steward": "Data Steward",
    "data_analyst": "Data Analyst",
    "normatividad_rag": "Normatividad",
    "document_intelligence": "Document Intelligence",
    "compliance": "Compliance",
}

_RE_TABLA = re.compile(
    r"\b(?:raw_|dim_|fact_|vw_)[a-z0-9_]+\b",
    re.IGNORECASE,
)
_RE_METADATA = re.compile(
    r"\b(?:tabla|tablas|columna|columnas|esquema|estructura|metadatos?|"
    r"filas?|registros?|faltantes?|nulos?|duplicad\w*|calidad|"
    r"relacion\w*|foreign\s+keys?)\b",
    re.IGNORECASE,
)
_RE_ANALITICA = re.compile(
    r"\b(?:cuantos?|cuantas?|total|suma|sumar|promedio|media|saldo|saldos|"
    r"monto|montos|distribucion|distribuyen|ranking|top|porcentaje|"
    r"metricas?|transacciones?|clientes?|cuentas?|movimientos?|"
    r"tendencia|comparacion|comparar)\b",
    re.IGNORECASE,
)
_RE_CALIDAD = re.compile(
    r"\b(?:duplicad\w*|faltantes?|nulos?|calidad|problemas?|"
    r"inconsistencias?)\b",
    re.IGNORECASE,
)
_RE_COMPLIANCE = re.compile(
    r"\b(?:kyc|aml|lavado|compliance|regulatori[oa]|"
    r"reportes?\s+regulatori[oa]s?|sanciones?)\b",
    re.IGNORECASE,
)
_RE_DOCUMENTO = re.compile(
    r"\b(?:factura|recibo|comprobante|documento|imagen|ocr|"
    r"folio|emisor|subtotal|iva)\b",
    re.IGNORECASE,
)
_RE_DOCUMENTO_AMPLIO = re.compile(
    r"\b(?:monto|total|fecha|importe|moneda|concepto)\b",
    re.IGNORECASE,
)
_RE_SEGUIMIENTO = re.compile(
    r"\b(?:si|no|por|solo|ahora|entonces|otra|otro|desglosa|desglosalo|"
    r"filtra|filtras|muestra|dame|agrega|quita|cambia)\b",
    re.IGNORECASE,
)

PROMPT_DIRECTO = """
Eres el nodo de respuesta directa de un sistema multi-agente bancario.

Responde en español, de forma breve y clara. Solo atiende saludos,
aclaraciones generales o preguntas que no correspondan a un especialista.
Si la consulta requiere datos, normativa, imágenes o compliance, dilo y pide
que se reformule para enviarla al especialista adecuado.

Si recibes contexto documental de un archivo ya procesado, úsalo como
evidencia y responde con él; no pidas reformular la consulta ni vuelvas a
procesar el archivo.

No inventes cifras, leyes, fuentes, tablas ni datos personales. Si no tienes
contexto suficiente, dilo explícitamente.
"""

PROMPT_DOCUMENTO = """
Eres el orquestador de un sistema multi-agente bancario. El subagente
document_intelligence ya procesó el archivo adjunto y te entrega su resultado.

Usa la instrucción del cliente y el resultado del subagente para decidir qué
hacer y responder al cliente. Si el cliente pidió OCR, transcripción,
descripción o Markdown, entrega eso; si pidió un dato o un resumen, responde
con la información relevante. Si no hay una instrucción clara, resume o
describe lo más útil.

No inventes datos que no aparezcan en el resultado. No muestres el texto
completo si el cliente no lo pidió.
"""


def _merge_dicts(actual: dict, nuevo: dict) -> dict:
    """Reducer de LangGraph: mezcla resultados sin perder especialistas."""
    return {**(actual or {}), **(nuevo or {})}


class EstadoGrafo(TypedDict, total=False):
    """Estado compartido por todos los nodos del grafo."""

    consulta: str
    historial: list
    contexto_documentos: str
    contexto_analista: str
    modelo: str
    perfil: dict
    imagen_pendiente: str | None
    ultimo_agente: str | None

    plan: list[str]
    rutas: list[str]
    pendientes: list[str]
    siguiente: str
    reintento: bool
    fallidos: list[str]
    intentos: int
    verificacion: dict

    resultados: Annotated[dict[str, dict], _merge_dicts]
    trazas: Annotated[list[dict], operator.add]
    errores: Annotated[list[str], operator.add]

    respuesta: str


def _texto(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            bloque.get("text", "")
            for bloque in content
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )
    return str(content or "")


def _normalizar_historial(historial: list) -> list[dict]:
    """Convierte mensajes de LangChain a dicts aceptados por ChatOllama."""
    normalizado: list[dict] = []
    for mensaje in historial or []:
        if isinstance(mensaje, dict):
            normalizado.append(
                {
                    "role": str(mensaje.get("role") or "user"),
                    "content": str(mensaje.get("content") or ""),
                }
            )
            continue

        tipo = str(getattr(mensaje, "type", "human"))
        rol = "assistant" if tipo in {"ai", "assistant"} else "user"
        normalizado.append(
            {"role": rol, "content": _texto(getattr(mensaje, "content", ""))}
        )
    return normalizado


def _tiene_ruta_imagen(consulta: str) -> bool:
    return bool(PATRON_RUTA_IMAGEN.search(consulta or ""))


def _ruta_imagen(consulta: str) -> str | None:
    coincidencia = PATRON_RUTA_IMAGEN.search(consulta or "")
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)


def _tiene_ruta_documento(consulta: str) -> bool:
    return bool(PATRON_RUTA_DOCUMENTO.search(consulta or ""))


def _ruta_documento(consulta: str) -> str | None:
    coincidencia = PATRON_RUTA_DOCUMENTO.search(consulta or "")
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)


def _tabla_mencionada(consulta: str) -> str:
    coincidencia = _RE_TABLA.search(consulta or "")
    return coincidencia.group(0) if coincidencia else ""


def _es_seguimiento(consulta: str) -> bool:
    texto = _normalizar(consulta or "")
    palabras = re.findall(r"[a-z0-9_]+", texto)
    return len(palabras) <= 8 and bool(_RE_SEGUIMIENTO.search(texto))


def _es_inventario_sql(texto: str) -> bool:
    return bool(
        re.search(r"\b(?:tablas|vistas)\b", texto)
        and re.search(
            r"\b(?:disponibles|existen|hay|listar|lista|listado|catalogo|inventario)\b",
            texto,
        )
        and not _RE_CALIDAD.search(texto)
        and not re.search(r"\b(?:csv|datasets?|archivos?)\b", texto)
    )


def planear_rutas(
    consulta: str,
    ultimo_agente: str | None = None,
    imagen_pendiente: str | None = None,
    contexto_documentos: str = "",
) -> list[str]:
    """Decide qué especialistas deben intervenir.

    Es un planificador determinista y auditable. La intención es que el grafo
    no dependa de que un modelo pequeño acierte la ruta; el LLM se reserva para
    la respuesta directa y para los especialistas que ya la usan internamente.
    """
    if imagen_pendiente or _tiene_ruta_imagen(consulta):
        return ["document_intelligence"]
    if _tiene_ruta_documento(consulta):
        return ["document_intelligence"]

    texto = _normalizar(consulta or "")

    # No enviar identificaciones de personas a la respuesta directa del LLM,
    # aunque el chat sea nuevo o antes se haya usado otro especialista.
    from proyecto.mcp_servers.data_analyst_backend import nombre_buscado
    if nombre_buscado(consulta):
        return ["data_analyst"]

    # Si ya hay un documento procesado, las preguntas sobre su contenido se
    # responden con el contexto documental; no deben desviarse a SQL.
    if contexto_documentos and (
        _RE_DOCUMENTO.search(texto)
        or (
            ultimo_agente == "document_intelligence"
            and _RE_DOCUMENTO_AMPLIO.search(texto)
        )
    ):
        return ["directo"]

    tabla = _tabla_mencionada(consulta)
    metadata = bool(_RE_METADATA.search(texto))
    analitica = bool(_RE_ANALITICA.search(texto))
    normativa = _es_consulta_normativa(consulta) or bool(re.search(r'\b(?:portal|terminos|historial crediticio)\b', texto))
    compliance = bool(_RE_COMPLIANCE.search(texto))
    inventario_sql = _es_inventario_sql(texto)

    if normativa:
        return ['normatividad_rag']
    bancaria = bool(re.search(r'\b(?:cuentas?|movimientos?|saldos?|gaste|gastos|retiros|estado de cuenta|perfil|rfc|promotores|sucursales)\b', texto))
    dataset = bool(tabla or re.search(r'\b(?:csv|archivo|dataset|pii|calidad|faltantes|nulos|duplicados|columnas)\b', texto))
    if bancaria and not dataset:
        return ['data_analyst']
    rutas: list[str] = []
    if normativa:
        rutas.append("normatividad_rag")
    if metadata and not inventario_sql:
        rutas.append("data_steward")

    # Si la consulta menciona una tabla concreta y pide metadatos (por ejemplo
    # "cuántas filas tiene raw_clientes"), Data Steward es el especialista
    # correcto; no hace falta mandarla también al Data Analyst.
    if inventario_sql or (analitica and not (metadata and tabla)):
        rutas.append("data_analyst")

    if compliance and not rutas:
        rutas.append("compliance")

    if not rutas and ultimo_agente and _es_seguimiento(consulta):
        if ultimo_agente in _ESPECIALISTAS:
            rutas.append(ultimo_agente)

    return list(dict.fromkeys(rutas)) or ["directo"]


def _traza(nombre: str, args: dict, salida: str, estado: str) -> dict:
    return {
        "name": nombre,
        "args": args,
        "salida": salida,
        "estado": estado,
    }


def _ejecutar_mcp(
    servidor: str,
    herramienta: str,
    argumentos: dict,
    nombre_traza: str,
) -> tuple[str, dict, str]:
    salida = _llamar_mcp(servidor, herramienta, argumentos)
    estado = "error" if salida.startswith("Error al llamar ") else "ok"
    if not salida.strip():
        estado = "error"
        salida = f"El servidor MCP {servidor} devolvió una salida vacía."
    return salida, _traza(nombre_traza, argumentos, salida, estado), estado


def _json_dict(salida: str) -> dict | None:
    try:
        datos = json.loads(salida)
    except (TypeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _solicita_grafica(consulta: str) -> bool:
    texto = _normalizar(consulta or "")
    return bool(
        re.search(
            r"\b(grafic\w*|chart\w*|plot\w*|visualiz\w*|diagram\w*|"
            r"barras|pastel|tendencia)\b",
            texto,
        )
    )


def _sql_de_texto(texto: str) -> str | None:
    """Extrae el último bloque SQL de la respuesta verificada del analista."""
    bloques = re.findall(
        r"```sql\s*(.*?)```",
        texto or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for bloque in reversed(bloques):
        candidato = bloque.strip().rstrip(";").strip()
        if re.match(r"(?is)^(select|with)\b", candidato):
            return candidato
    return None


def _grafica_de_datos(datos: dict | None) -> tuple[bytes | None, dict, dict | None]:
    if not isinstance(datos, dict) or datos.get("error"):
        return None, {}, datos
    try:
        png = base64.b64decode(str(datos.get("png_base64") or ""))
    except Exception:  # noqa: BLE001 - salida externa mal formada
        return None, {}, datos
    if not png:
        return None, {}, datos
    especificacion = datos.get("especificacion")
    return png, dict(especificacion or {}), datos


def _validar_salida_steward(herramienta: str, datos: dict) -> str | None:
    """Contrasta el contrato de las tools antes de formatear la respuesta.

    Solo se publican agregados cuyos tipos y rangos son consistentes. Una
    salida fuera de contrato se reporta como error y no se formatea, de modo
    que la respuesta nunca presente una cifra que la tool no devolvió.
    """
    if herramienta == "inspeccionar_dataset":
        filas = datos.get("filas")
        if type(filas) is not int or filas < 0:
            return "filas debe ser un entero no negativo"
        columnas = datos.get("columnas")
        if not isinstance(columnas, list) or not columnas:
            return "columnas debe ser una lista no vacía"
        for columna in columnas:
            if not isinstance(columna, dict):
                return "columna inválida"
            nombre = columna.get("nombre")
            if not isinstance(nombre, str) or not nombre:
                return "columna sin nombre"
            faltantes = columna.get("valores_faltantes")
            if type(faltantes) is not int or not 0 <= faltantes <= filas:
                return f"valores_faltantes inválido en {nombre!r}"
        return None

    if herramienta == "validar_calidad_datos":
        filas = datos.get("filas")
        if type(filas) is not int or filas < 0:
            return "filas debe ser un entero no negativo"
        for clave in (
            "filas_con_algun_valor_faltante",
            "filas_duplicadas_exactas",
        ):
            valor = datos.get(clave)
            if type(valor) is not int or not 0 <= valor <= filas:
                return f"{clave} debe ser un entero entre 0 y filas"
        sobrantes = datos.get("filas_sobrantes_por_clave")
        if sobrantes is not None and (
            type(sobrantes) is not int or not 0 <= sobrantes <= filas
        ):
            return "filas_sobrantes_por_clave debe ser nulo o un entero entre 0 y filas"
        faltantes = datos.get("valores_faltantes_por_columna")
        if not isinstance(faltantes, dict):
            return "valores_faltantes_por_columna debe ser un objeto"
        for nombre, valor in faltantes.items():
            if not isinstance(nombre, str) or not nombre:
                return "valores_faltantes_por_columna tiene una columna sin nombre"
            if type(valor) is not int or not 0 <= valor <= filas:
                return f"faltantes inválidos en {nombre!r}"
        return None

    if herramienta == "evaluar_calidad":
        evaluaciones = datos.get("evaluaciones")
        if not isinstance(evaluaciones, list):
            return "evaluaciones debe ser una lista"
        for evaluacion in evaluaciones:
            if not isinstance(evaluacion, dict):
                return "evaluación inválida"
            if type(evaluacion.get("filas_afectadas")) is not int:
                return "filas_afectadas debe ser un entero"
            encontradas = evaluacion.get("encontradas")
            if encontradas is not None and type(encontradas) is not int:
                return "encontradas debe ser nulo o un entero"
            if not isinstance(evaluacion.get("coincide"), bool):
                return "coincide debe ser booleano"
        resumen = datos.get("resumen")
        if not isinstance(resumen, dict):
            return "resumen debe ser un objeto"
        return None

    return None


def _nodo_planear(estado: EstadoGrafo) -> dict:
    if estado.get("reintento"):
        fallidos = [
            ruta
            for ruta in estado.get("fallidos") or []
            if ruta in _ESPECIALISTAS
        ]
        return {"pendientes": fallidos, "reintento": False}

    rutas = planear_rutas(
        estado.get("consulta") or "",
        ultimo_agente=estado.get("ultimo_agente"),
        imagen_pendiente=estado.get("imagen_pendiente"),
        contexto_documentos=estado.get("contexto_documentos") or "",
    )
    return {
        "rutas": rutas,
        "plan": rutas,
        "pendientes": rutas,
        "reintento": False,
    }


def _nodo_despachar(estado: EstadoGrafo) -> dict:
    pendientes = list(estado.get("pendientes") or [])
    if not pendientes:
        return {"siguiente": "verificar", "pendientes": []}
    siguiente = pendientes.pop(0)
    if siguiente not in _ESPECIALISTAS:
        return {"siguiente": "verificar", "pendientes": pendientes}
    return {"siguiente": siguiente, "pendientes": pendientes}


def _siguiente_despacho(estado: EstadoGrafo) -> str:
    siguiente = estado.get("siguiente") or "verificar"
    return siguiente if siguiente in _ESPECIALISTAS else "verificar"


def _nodo_data_steward(estado: EstadoGrafo) -> dict:
    consulta = estado.get("consulta") or ""
    tabla = _tabla_mencionada(consulta)
    pide_calidad = bool(_RE_CALIDAD.search(_normalizar(consulta)))

    llamadas = [
        (
            "inspeccionar_dataset",
            "mcp_inspeccionar_dataset",
            {"tabla": tabla},
        )
    ]
    if pide_calidad:
        llamadas.append(
            (
                "validar_calidad_datos",
                "mcp_validar_calidad_datos",
                {"tabla": tabla},
            )
        )
        llamadas.append(
            (
                "evaluar_calidad",
                "mcp_evaluar_calidad",
                {"tabla": tabla},
            )
        )

    resultados_json: list[dict] = []
    salidas: list[str] = []
    trazas: list[dict] = []
    errores: list[str] = []

    for herramienta, nombre_traza, argumentos in llamadas:
        salida, traza, estado_llamada = _ejecutar_mcp(
            "data_steward_server",
            herramienta,
            argumentos,
            nombre_traza,
        )
        trazas.append(traza)
        salidas.append(salida)
        datos = _json_dict(salida)
        if (
            estado_llamada == "error"
            or not isinstance(datos, dict)
            or "error" in datos
        ):
            detalle = (
                str(datos.get("error"))
                if isinstance(datos, dict) and datos.get("error")
                else salida
            )
            errores.append(f"{herramienta}: {detalle}")
            continue
        fuera_de_contrato = _validar_salida_steward(herramienta, datos)
        if fuera_de_contrato:
            errores.append(
                f"{herramienta}: salida fuera de contrato ({fuera_de_contrato})"
            )
            continue
        resultados_json.append({"herramienta": herramienta, "datos": datos})

    if resultados_json:
        try:
            respuesta = data_steward_agent.formatear_resultados_verificados(
                resultados_json
            )
        except Exception as error:  # noqa: BLE001 - se conserva la salida cruda
            respuesta = "\n\n".join(salidas)
            errores.append(f"formateo: {type(error).__name__}: {error}")
        if errores:
            respuesta += "\n\n" + "\n".join(
                f"- No se pudo completar: {error}" for error in errores
            )
        estado_resultado = "ok"
    else:
        respuesta = "\n".join(
            [f"- {error}" for error in errores]
            or ["No se obtuvieron datos de las herramientas de metadatos."]
        )
        estado_resultado = "error"

    return {
        "resultados": {
            "data_steward": {
                "agente": "data_steward",
                "respuesta": respuesta,
                "estado": estado_resultado,
            }
        },
        "trazas": trazas,
        "errores": errores,
    }


def _contexto_previo(estado: EstadoGrafo) -> str:
    partes: list[str] = []
    for agente, resultado in (estado.get("resultados") or {}).items():
        if agente == "data_analyst" or resultado.get("estado") == "error":
            continue
        texto = str(resultado.get("respuesta") or "").strip()
        if texto:
            partes.append(f"{agente}: {texto[:1200]}")
    return "\n\n".join(partes)


@lru_cache(maxsize=32)
def _hub_por_sesion(clave: str) -> HubDataAnalyst:
    return HubDataAnalyst()


def _get_hub_data_analyst() -> HubDataAnalyst:
    from proyecto.security.local_auth import principal, TOKEN
    principal()
    token = TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    return _hub_por_sesion(hashlib.sha256(token.encode()).hexdigest())


def _nodo_data_analyst_con_hub(estado: EstadoGrafo) -> dict:
    """Ruta alternativa para comparar el hub con el flujo MCP anterior.

    Se activa con ``DATA_ANALYST_HUB=1``. El hub usa las tools SQL de bajo
    nivel y contrasta el SQL/datos declarados con la ejecución real.
    """
    consulta = estado.get("consulta") or ""
    tarea = Tarea(
        id="data_analyst",
        dominio=DominioTarea.DATA_ANALYST,
        instruccion=consulta,
        objetivo="Responder la consulta cuantitativa con evidencia SQL.",
        resultado_esperado=(
            "Respuesta con cifras verificables, SQL ejecutado y datos tabulares."
        ),
        contexto=estado.get("contexto_analista") or "",
        restricciones=[
            "No inventes cifras, tablas ni columnas.",
            "Aplica el alcance del perfil autenticado.",
        ],
        evidencia_requerida=["consulta SQL ejecutada", "resultado de la consulta"],
    )
    contexto = ContextoHub(
        session_id="grafo",
        perfil=estado.get("perfil") or {},
        slots={"consulta": consulta},
        ultima_intencion=estado.get("ultimo_agente"),
    )
    resultado: ResultadoTarea = _get_hub_data_analyst().ejecutar(tarea, contexto)

    ok = resultado.estado is EstadoResultado.OK
    detalle = resultado.respuesta if ok else (
        resultado.error or resultado.respuesta or "El hub no pudo completar la tarea."
    )
    return {
        "resultados": {
            "data_analyst": {
                "agente": "data_analyst",
                "respuesta": detalle,
                "estado": "ok" if ok else "error",
                "sql": resultado.sql,
                "datos": resultado.datos,
                "grafica": resultado.grafica,
                "grafica_especificacion": resultado.grafica_especificacion,
                "evidencia": resultado.evidencia,
                "duracion": resultado.duracion,
            }
        },
        "trazas": resultado.trazas,
        "errores": [] if ok else [f"data_analyst: {detalle}"],
    }


def _nodo_data_analyst(estado: EstadoGrafo) -> dict:
    if os.environ.get("DATA_ANALYST_HUB") == "1":
        from proyecto.agents.banking_queries import plan_query
        from proyecto.mcp_servers.data_analyst_backend import nombre_buscado
        consulta = estado.get("consulta") or ""
        if not plan_query(consulta) and not nombre_buscado(consulta):
            return _nodo_data_analyst_con_hub(estado)

    if _es_inventario_sql(_normalizar(estado.get("consulta") or "")):
        salida, traza, estado_llamada = _ejecutar_mcp(
            "data_analyst_server", "listar_tablas", {}, "mcp_listar_tablas"
        )
        datos = _json_dict(salida)
        valido = (
            estado_llamada == "ok"
            and datos is not None
            and "error" not in datos
            and isinstance(datos.get("motor"), str)
            and all(
                isinstance(datos.get(tipo), list)
                and all(isinstance(nombre, str) and nombre for nombre in datos[tipo])
                and len(set(datos[tipo])) == len(datos[tipo])
                for tipo in ("tablas", "vistas")
            )
        )
        if valido:
            # El JSON MCP es la fuente de verdad; no se resume con un modelo.
            respuesta = (
                f"Motor: {datos['motor']}. "
                f"Inventario disponible: {len(datos['tablas'])} tablas y "
                f"{len(datos['vistas'])} vistas."
            )
            for tipo in ("tablas", "vistas"):
                respuesta += f"\n\n**{tipo.capitalize()} ({len(datos[tipo])})**\n"
                respuesta += "\n".join(f"- `{nombre}`" for nombre in datos[tipo]) or "(ninguna)"
        else:
            estado_llamada = "error"
            respuesta = "No se pudo verificar el inventario de tablas y vistas devuelto por MCP."
            traza["estado"] = "error"
        return {
            "resultados": {"data_analyst": {
                "agente": "data_analyst", "respuesta": respuesta, "estado": estado_llamada,
            }},
            "trazas": [traza],
            "errores": [respuesta] if estado_llamada == "error" else [],
        }
    contexto = estado.get("contexto_analista") or ""
    previo = _contexto_previo(estado)
    if previo:
        contexto = (
            f"{contexto}\n\nResultados previos de otros especialistas:\n{previo}"
        ).strip()
    argumentos = {
        "consulta": estado.get("consulta") or "",
        "contexto": contexto,
        "perfil": estado.get("perfil") or {},
    }
    salida, traza, estado_llamada = _ejecutar_mcp(
        "data_analyst_server",
        "analizar_datos",
        argumentos,
        "mcp_analizar_datos",
    )
    salida = limpiar_base64(salida)
    trazas = [traza]
    resultado_analista = {
        "agente": "data_analyst",
        "respuesta": salida,
        "estado": estado_llamada,
    }

    # La gráfica es una tool determinista y opcional. Si el usuario la pidió,
    # reutilizamos el SQL verificado de la respuesta; nunca inventamos otro.
    sql = _sql_de_texto(salida) if estado_llamada == "ok" else None
    if sql and _solicita_grafica(estado.get("consulta") or ""):
        argumentos_grafica = {
            "sql": sql,
            "tipo": "auto",
            "perfil": estado.get("perfil") or {},
        }
        salida_grafica, traza_grafica, estado_grafica = _ejecutar_mcp(
            "data_analyst_server",
            "graficar_consulta",
            argumentos_grafica,
            "mcp_graficar_consulta",
        )
        datos_grafica = _json_dict(salida_grafica)
        png, especificacion, datos_validos = _grafica_de_datos(datos_grafica)
        if png is not None and datos_validos is not None:
            resultado_analista.update({
                "sql": sql,
                "datos": {
                    "columnas": datos_validos.get("columnas") or [],
                    "filas": datos_validos.get("filas") or [],
                    "truncado": False,
                },
                "grafica": png,
                "grafica_especificacion": especificacion,
            })
        else:
            traza_grafica["estado"] = "error"
            resultado_analista["grafica_error"] = (
                str((datos_grafica or {}).get("error") or salida_grafica)
            )
        trazas.append(traza_grafica)

    return {
        "resultados": {"data_analyst": resultado_analista},
        "trazas": trazas,
        "errores": [f"data_analyst: {salida}"] if estado_llamada == "error" else [],
    }


def _nodo_normatividad(estado: EstadoGrafo) -> dict:
    argumentos = {"query": estado.get("consulta") or ""}
    salida, traza, estado_llamada = _ejecutar_mcp(
        "normatividad_rag_server",
        "buscar_normatividad",
        argumentos,
        "mcp_buscar_normatividad",
    )
    return {
        "resultados": {
            "normatividad_rag": {
                "agente": "normatividad_rag",
                "respuesta": salida,
                "estado": estado_llamada,
            }
        },
        "trazas": [traza],
        "errores": (
            [f"normatividad_rag: {salida}"]
            if estado_llamada == "error"
            else []
        ),
    }


def _nodo_documento(estado: EstadoGrafo) -> dict:
    consulta = estado.get("consulta") or ""
    ruta = estado.get("imagen_pendiente") or _ruta_imagen(consulta)

    if not ruta:
        ruta_documento = _ruta_documento(consulta)
        if ruta_documento:
            salida, traza, estado_llamada = _ejecutar_mcp(
                "document_intelligence_server",
                "convertir_documento_markdown",
                {"file_path": ruta_documento},
                "mcp_convertir_documento_markdown",
            )
            if salida.startswith("No se pudo convertir el documento"):
                estado_llamada = "error"
            return {
                "resultados": {
                    "document_intelligence": {
                        "agente": "document_intelligence",
                        "respuesta": salida,
                        "estado": estado_llamada,
                    }
                },
                "trazas": [traza],
                "errores": (
                    [f"document_intelligence: {salida}"]
                    if estado_llamada == "error"
                    else []
                ),
            }

        respuesta = (
            "Proporciona la ruta local de una imagen o documento "
            "(por ejemplo: tests/modelo-factura-ex.png o "
            "data_governance_files/archivo.pdf)."
        )
        return {
            "resultados": {
                "document_intelligence": {
                    "agente": "document_intelligence",
                    "respuesta": respuesta,
                    "estado": "error",
                }
            },
            "trazas": [],
            "errores": ["document_intelligence: falta la ruta del archivo"],
        }

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
        _normalizar(palabra) in texto for palabra in PALABRAS_DESCRIPCION
    )
    describir = pide_descripcion and not pide_ocr
    herramienta = "describir_imagen" if describir else "extraer_texto_imagen"
    nombre_traza = (
        "mcp_describir_imagen"
        if describir
        else "mcp_extraer_texto_imagen"
    )
    salida, traza, estado_llamada = _ejecutar_mcp(
        "document_intelligence_server",
        herramienta,
        {"image_path": ruta},
        nombre_traza,
    )
    if salida.startswith("No se pudo procesar la imagen:"):
        estado_llamada = "error"
    return {
        "resultados": {
            "document_intelligence": {
                "agente": "document_intelligence",
                "respuesta": salida,
                "estado": estado_llamada,
            }
        },
        "trazas": [traza],
        "errores": (
            [f"document_intelligence: {salida}"]
            if estado_llamada == "error"
            else []
        ),
    }


def _nodo_compliance(estado: EstadoGrafo) -> dict:
    respuesta = (
        "El módulo de compliance, KYC y AML está fuera del alcance del MVP "
        "actual (fase 2). No puedo emitir una evaluación regulatoria ni "
        "confirmar cumplimiento con esa normativa."
    )
    return {
        "resultados": {
            "compliance": {
                "agente": "compliance",
                "respuesta": respuesta,
                "estado": "ok",
            }
        },
        "trazas": [],
        "errores": [],
    }


def _nodo_verificar(estado: EstadoGrafo) -> dict:
    plan = [
        ruta
        for ruta in estado.get("plan") or []
        if ruta in _ESPECIALISTAS
    ]
    resultados = estado.get("resultados") or {}
    fallidos = [
        ruta
        for ruta in plan
        if ruta not in resultados
        or resultados[ruta].get("estado") == "error"
    ]
    intentos = int(estado.get("intentos") or 0)

    if fallidos and intentos < 1:
        return {
            "reintento": True,
            "fallidos": fallidos,
            "intentos": intentos + 1,
            "verificacion": {
                "estado": "reintentar",
                "fallidos": fallidos,
            },
        }

    return {
        "reintento": False,
        "verificacion": {
            "estado": "ok" if not fallidos else "incompleto",
            "fallidos": fallidos,
        },
    }


def _decidir_verificacion(estado: EstadoGrafo) -> str:
    return "reintentar" if estado.get("reintento") else "sintetizar"


def _respuesta_directa(estado: EstadoGrafo) -> str:
    historial = _normalizar_historial(estado.get("historial") or [])
    if not historial:
        historial = [{"role": "user", "content": estado.get("consulta") or ""}]

    contexto = str(estado.get("contexto_documentos") or "").strip()
    system = PROMPT_DIRECTO
    if contexto:
        system += "\n\nContexto documental de la conversación:\n" + contexto

    mensajes = [{"role": "system", "content": system}, *historial]
    try:
        respuesta = ChatOllama(
            model=estado.get("modelo") or modelo_de("orchestrator"),
            temperature=0,
            reasoning=False,
            timeout=60,
        ).invoke(mensajes)
        texto = _texto(getattr(respuesta, "content", respuesta)).strip()
        return texto or "No pude generar una respuesta directa."
    except Exception as error:  # noqa: BLE001 - se informa al usuario
        return (
            "No pude generar una respuesta directa: "
            f"{type(error).__name__}: {error}"
        )


def _respuesta_documento(estado: EstadoGrafo, resultado: dict) -> str:
    """Sintetiza el resultado del subagente document_intelligence."""
    consulta = str(estado.get("consulta") or "").strip()
    salida = str(resultado.get("respuesta") or "").strip()
    if not salida:
        return "El subagente document_intelligence no devolvió contenido."

    if len(salida) > 12000:
        salida = salida[:12000].rstrip() + "\n[…resultado truncado…]"

    mensajes = [
        {"role": "system", "content": PROMPT_DOCUMENTO},
        {
            "role": "user",
            "content": (
                f"Instrucción del cliente:\n{consulta or '(sin instrucción)'}\n\n"
                "Resultado del subagente document_intelligence:\n"
                f"{salida}"
            ),
        },
    ]
    try:
        respuesta = ChatOllama(
            model=estado.get("modelo") or modelo_de("orchestrator"),
            temperature=0,
            reasoning=False,
            timeout=60,
        ).invoke(mensajes)
        texto = _texto(getattr(respuesta, "content", respuesta)).strip()
        return texto or salida
    except Exception as error:  # noqa: BLE001 - se informa en la UI
        return (
            "No pude generar la respuesta final con el orquestador: "
            f"{type(error).__name__}: {error}\n\n"
            "Resultado del subagente document_intelligence:\n"
            f"{salida}"
        )


def _nodo_sintetizar(estado: EstadoGrafo) -> dict:
    plan = list(estado.get("plan") or [])
    resultados = estado.get("resultados") or {}

    if not plan or plan == ["directo"]:
        return {"respuesta": _respuesta_directa(estado)}

    if plan == ["document_intelligence"]:
        resultado = resultados.get("document_intelligence") or {}
        if resultado.get("estado") != "error" and resultado.get("respuesta"):
            return {"respuesta": _respuesta_documento(estado, resultado)}

    partes: list[str] = []
    for agente in plan:
        resultado = resultados.get(agente)
        etiqueta = _ETIQUETAS.get(agente, agente)
        if not resultado:
            partes.append(
                f"### {etiqueta}\nNo se obtuvo respuesta de este especialista."
            )
            continue
        if resultado.get("estado") == "error":
            partes.append(
                f"### {etiqueta}\nNo se pudo completar: "
                f"{resultado.get('respuesta', 'error desconocido')}"
            )
            continue
        partes.append(f"### {etiqueta}\n{resultado.get('respuesta', '')}")

    if len(partes) == 1:
        resultado = resultados.get(plan[0]) or {}
        respuesta = str(resultado.get("respuesta") or "").strip()
    else:
        respuesta = "\n\n".join(partes).strip()

    if not respuesta:
        respuesta = (
            "El grafo no obtuvo una respuesta de los especialistas. "
            "Revisa la traza para ver el error."
        )
    return {"respuesta": respuesta}


_GRAFO = None


def _construir_grafo():
    grafo = StateGraph(EstadoGrafo)

    grafo.add_node("planear", _nodo_planear)
    grafo.add_node("despachar", _nodo_despachar)
    grafo.add_node("data_steward", _nodo_data_steward)
    grafo.add_node("data_analyst", _nodo_data_analyst)
    grafo.add_node("normatividad_rag", _nodo_normatividad)
    grafo.add_node("document_intelligence", _nodo_documento)
    grafo.add_node("compliance", _nodo_compliance)
    grafo.add_node("verificar", _nodo_verificar)
    grafo.add_node("sintetizar", _nodo_sintetizar)

    grafo.add_edge(START, "planear")
    grafo.add_edge("planear", "despachar")
    grafo.add_conditional_edges(
        "despachar",
        _siguiente_despacho,
        {
            "data_steward": "data_steward",
            "data_analyst": "data_analyst",
            "normatividad_rag": "normatividad_rag",
            "document_intelligence": "document_intelligence",
            "compliance": "compliance",
            "verificar": "verificar",
        },
    )

    for especialista in (
        "data_steward",
        "data_analyst",
        "normatividad_rag",
        "document_intelligence",
        "compliance",
    ):
        grafo.add_edge(especialista, "despachar")

    grafo.add_conditional_edges(
        "verificar",
        _decidir_verificacion,
        {
            "reintentar": "planear",
            "sintetizar": "sintetizar",
        },
    )
    grafo.add_edge("sintetizar", END)
    return grafo.compile()


def _get_grafo():
    global _GRAFO
    if _GRAFO is None:
        _GRAFO = _construir_grafo()
    return _GRAFO


def ejecutar_grafo(
    consulta: str,
    *,
    historial: list | None = None,
    contexto_documentos: str = "",
    contexto_analista: str = "",
    modelo: str | None = None,
    imagen_pendiente: str | None = None,
    ultimo_agente: str | None = None,
    perfil: dict | None = None,
) -> EstadoGrafo:
    """Ejecuta el grafo para una consulta y devuelve el estado final."""
    estado_inicial: EstadoGrafo = {
        "consulta": consulta,
        "historial": list(historial or []),
        "contexto_documentos": contexto_documentos,
        "contexto_analista": contexto_analista,
        "modelo": modelo or modelo_de("orchestrator"),
        "perfil": dict(perfil or {}),
        "imagen_pendiente": (
            str(Path(imagen_pendiente).expanduser().resolve())
            if imagen_pendiente
            else None
        ),
        "ultimo_agente": ultimo_agente,
        "plan": [],
        "rutas": [],
        "pendientes": [],
        "siguiente": "",
        "reintento": False,
        "fallidos": [],
        "intentos": 0,
        "resultados": {},
        "trazas": [],
        "errores": [],
        "respuesta": "",
    }
    return _get_grafo().invoke(
        estado_inicial,
        config={
            "run_name": "orquestador_grafo",
            "tags": ["orquestador", "grafo", "ui"],
            "metadata": {"modelo": modelo},
            "recursion_limit": 20,
        },
    )
