"""Data Steward over the synthetic Banamex database.

Este agente revisa la estructura y la calidad de la base sintética (tablas,
columnas, faltantes y duplicados) y solo devuelve agregados; nunca valores de
registros individuales. El CSV de clientes fue retirado del flujo.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from sqlalchemy import inspect, text

from proyecto.config.models import modelo_de

from .database import (
    DEFAULT_DB_NAME,
    create_readonly_engine,
    get_database_url,
    objetos_disponibles,
)

SYSTEM_PROMPT = """
Eres el Data Steward de la base sintética bancaria de Banamex.

Tu dominio es la estructura y la calidad de los datos:
- qué tablas y vistas existen;
- columnas, tipos y número de filas;
- valores faltantes, filas duplicadas y claves repetidas;
- observaciones de calidad, sin revelar registros individuales.

Usa las herramientas para obtener los hechos. No inventes columnas, tablas,
reglas de negocio ni causas. Distingue un conteo de faltantes de una fila con
algún faltante. Un duplicado o una clave repetida requiere revisión; no implica
borrar datos.

En `inspeccionar_dataset`, `catalogo_tablas` es solo el inventario de tablas y
vistas disponibles: no son relaciones. Las relaciones reales son las que
aparecen en `relaciones` (foreign keys declaradas). Si `relaciones` está vacío,
di que no hay foreign keys declaradas; no deduzcas relaciones por la lista de
tablas. Si la consulta menciona faltantes, nulos, duplicados, calidad o
problemas, llama a `validar_calidad_datos` además de inspeccionar la tabla. Si
la tabla es una de las capas crudas (`raw_*`) y existe un hallazgo esperado,
usa `evaluar_calidad` para comparar lo encontrado contra
`calidad_hallazgos_esperados`; no inventes el número esperado.

Nunca muestres nombres, correos, teléfonos, CURP, RFC, direcciones ni valores
de registros individuales. Trabaja con agregados y metadatos. No modifiques la
base ni afirmes haberlo hecho.
"""

# El Data Steward sí puede revisar la capa cruda porque ahí están los defectos
# de calidad; sus herramientas solo devuelven conteos y metadatos. La tabla de
# personas queda fuera para no exponer datos individuales.
TABLAS_EXCLUIDAS_STEWARD = {"dim_cliente_persona"}

_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        url_db = get_database_url()
        if not url_db:
            raise RuntimeError(
                "No hay base configurada: define DATABASE_URL o genera "
                "proyecto/bd/banamex.db con proyecto/bd/cargar_bd.py."
            )
        _ENGINE = create_readonly_engine(url_db)
    return _ENGINE


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


_CIFRAS = re.compile(r"\d")
_NOMBRE_TABLA = re.compile(r"\b(?:raw_|dim_|fact_|vw_)[a-z0-9_]+\b", re.IGNORECASE)
_PALABRAS_METADATA = re.compile(
    r"\b(?:tabla|columna|filas?|registros?|faltantes?|nulos?|"
    r"duplicad\w*|relacion\w*|foreign\s+keys?)\b",
    re.IGNORECASE,
)
_SOLICITA_CALIDAD = re.compile(
    r"\b(?:duplicad\w*|faltantes?|nulos?|calidad|problemas?|"
    r"inconsistencias?)\b",
    re.IGNORECASE,
)
_MENSAJE_SIN_HERRAMIENTA = (
    "No consulté las herramientas de metadatos en este turno, así que no "
    "tengo datos verificados para responder. Para evitar inventar tablas, "
    "columnas, faltantes o relaciones, no voy a dar detalles. Reformula la "
    "pregunta indicando la tabla o vista que quieres revisar y lo intento de "
    "nuevo."
)


def _parece_respuesta_sin_herramienta(texto: str) -> bool:
    return bool(
        _CIFRAS.search(texto)
        or (
            _NOMBRE_TABLA.search(texto)
            and _PALABRAS_METADATA.search(texto)
        )
    )


def _solicita_registros_personales(consulta: str) -> bool:
    """Bloquea solicitudes de valores personales o registros individuales."""
    texto = _normalizar(consulta)
    campos_personales = re.search(
        r"\b(?:nombres?|correos?|emails?|telefonos?|celulares?|"
        r"direcciones?|domicilios?|curp|rfc|datos personales|"
        r"registros? personales|informacion personal)\b",
        texto,
    )
    accion_de_revelar = re.search(
        r"\b(?:muestr\w*|mostr\w*|dame|give|show|list\w*|enumera\w*|"
        r"proporcion\w*|revela\w*|comparte\w*|imprime\w*|exporta\w*|"
        r"descarga\w*|extrae\w*|lee|obt[e]?n\w*|recupera\w*|ver)\b",
        texto,
    )
    pregunta_por_valores = re.search(
        r"\b(?:cuales? son|que\s+\w+\s+(?:hay|tiene|aparecen)|"
        r"what\s+(?:are|is)|which\s+\w+\s+(?:are|is))\b",
        texto,
    )
    return bool(campos_personales and (accion_de_revelar or pregunta_por_valores))


def _resolver_tabla(tabla: str):
    engine = _get_engine()
    inspector = inspect(engine)
    tablas, vistas = objetos_disponibles(
        inspector, excluir=TABLAS_EXCLUIDAS_STEWARD
    )
    disponibles = sorted(tablas + vistas)
    if not disponibles:
        return engine, inspector, "", [], [], [], "No hay tablas disponibles."

    solicitada = (tabla or "").strip()
    if not solicitada:
        for candidata in ("raw_clientes", "vw_cliente_publico"):
            if candidata in disponibles:
                solicitada = candidata
                break
        else:
            solicitada = disponibles[0]

    if solicitada not in disponibles:
        return (
            engine,
            inspector,
            solicitada,
            tablas,
            vistas,
            disponibles,
            f"La tabla '{solicitada}' no existe o no está disponible.",
        )
    return engine, inspector, solicitada, tablas, vistas, disponibles, None


def _relaciones_tabla(inspector, tabla: str) -> list[dict]:
    return [
        {
            "columnas": fk["constrained_columns"],
            "referencia": (
                f"{fk['referred_table']}("
                f"{', '.join(fk['referred_columns'])})"
            ),
        }
        for fk in inspector.get_foreign_keys(tabla)
    ]


@tool
def inspeccionar_dataset(tabla: str = "") -> str:
    """Devuelve tablas/vistas, columnas, tipos, filas y faltantes de la base sintética.

    Args:
        tabla: Tabla o vista a inspeccionar. Vacío usa raw_clientes.
    """
    try:
        engine, inspector, nombre, tablas, vistas, disponibles, error = _resolver_tabla(tabla)
        if error:
            return json.dumps(
                {"error": error, "catalogo_tablas": disponibles},
                ensure_ascii=False,
            )

        q = engine.dialect.identifier_preparer.quote
        columnas = []
        with engine.connect() as conexion:
            filas = conexion.exec_driver_sql(
                f"SELECT COUNT(*) FROM {q(nombre)}"
            ).scalar()
            for columna in inspector.get_columns(nombre):
                nombre_columna = columna["name"]
                faltantes = conexion.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {q(nombre)} "
                    f"WHERE {q(nombre_columna)} IS NULL"
                ).scalar()
                columnas.append(
                    {
                        "nombre": nombre_columna,
                        "tipo": str(columna["type"]),
                        "valores_faltantes": int(faltantes or 0),
                    }
                )

        return json.dumps(
            {
                "base": DEFAULT_DB_NAME,
                "motor": engine.dialect.name,
                "tabla": nombre,
                "es_vista": nombre in vistas,
                "filas": int(filas or 0),
                "columnas": columnas,
                "relaciones": _relaciones_tabla(inspector, nombre),
                "catalogo_tablas": disponibles,
                "nota_catalogo": (
                    "catalogo_tablas es solo el inventario de tablas y vistas "
                    "disponibles; no implica que estén relacionadas con la "
                    "tabla inspeccionada. Usa relaciones para las foreign keys."
                ),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as error:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(error).__name__}: {error}"},
            ensure_ascii=False,
        )


FECHA_CORTE_CALIDAD = date(2026, 12, 31)


def _esperados_calidad(engine, tabla: str) -> list[dict]:
    """Lee los hallazgos esperados declarados para una tabla."""
    sql = text(
        "SELECT id, tabla, columna, tipo_defecto, filas_afectadas, descripcion "
        "FROM calidad_hallazgos_esperados WHERE tabla = :tabla ORDER BY id"
    )
    try:
        with engine.connect() as conexion:
            return [
                dict(fila)
                for fila in conexion.execute(sql, {"tabla": tabla}).mappings()
            ]
    except Exception:  # noqa: BLE001 - la tabla de expectativas es opcional
        return []


def _clave_tabla(
    inspector, tabla: str, esperados: list[dict] | None = None
) -> tuple[list[str], str]:
    """Devuelve la clave declarada, no una heurística por nombre de columna."""
    columnas = {columna["name"] for columna in inspector.get_columns(tabla)}
    pk = inspector.get_pk_constraint(tabla).get("constrained_columns") or []
    if pk and all(columna in columnas for columna in pk):
        return list(pk), "primary_key"

    for restriccion in inspector.get_unique_constraints(tabla):
        clave = restriccion.get("column_names") or []
        if clave and all(columna in columnas for columna in clave):
            return list(clave), "unique_constraint"

    for esperado in esperados or []:
        if esperado.get("tipo_defecto") != "fila_duplicada":
            continue
        columnas_esperadas = str(esperado.get("columna") or "")
        if not columnas_esperadas or columnas_esperadas == "*":
            continue
        clave = [
            columna.strip()
            for columna in columnas_esperadas.split(",")
            if columna.strip()
        ]
        if clave and all(columna in columnas for columna in clave):
            return clave, "calidad_hallazgos_esperados"

    return [], "sin_clave"


def _sobrantes_por_clave(engine, tabla: str, clave: list[str]) -> int:
    """Cuenta filas sobrantes por una clave declarada."""
    q = engine.dialect.identifier_preparer.quote
    grupo = ", ".join(q(columna) for columna in clave)
    sql = (
        "SELECT COALESCE(SUM(n - 1), 0) FROM ("
        f"SELECT {grupo}, COUNT(*) AS n FROM {q(tabla)} "
        f"GROUP BY {grupo} HAVING COUNT(*) > 1"
        ") AS sobrantes"
    )
    with engine.connect() as conexion:
        return int(conexion.exec_driver_sql(sql).scalar() or 0)


def _evaluar_hallazgo(
    engine, tabla: str, hallazgo: dict, columnas: list[str]
) -> dict:
    """Calcula de forma determinista un hallazgo esperado."""
    q = engine.dialect.identifier_preparer.quote
    tipo = str(hallazgo.get("tipo_defecto") or "")
    columna = str(hallazgo.get("columna") or "")
    encontradas: int | None = None
    detalle = ""

    if tipo == "valor_nulo":
        if columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": f"La columna '{columna}' no existe."}
        sql = (
            f"SELECT COUNT(*) FROM {q(tabla)} "
            f"WHERE {q(columna)} IS NULL"
        )
    elif tipo == "fila_duplicada":
        if columna == "*":
            sql = (
                f"SELECT COUNT(*) - (SELECT COUNT(*) FROM "
                f"(SELECT DISTINCT * FROM {q(tabla)}) AS distintas) "
                f"FROM {q(tabla)}"
            )
            detalle = "filas exactas duplicadas"
        else:
            clave = [
                parte.strip()
                for parte in columna.split(",")
                if parte.strip()
            ]
            if not clave or not all(parte in columnas for parte in clave):
                return {**hallazgo, "encontradas": None, "coincide": False,
                        "detalle": f"La clave '{columna}' no existe en la tabla."}
            encontradas = _sobrantes_por_clave(engine, tabla, clave)
            detalle = "filas sobrantes por clave"
    elif tipo == "fecha_futura":
        if columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": f"La columna '{columna}' no existe."}
        sql = (
            f"SELECT COUNT(*) FROM {q(tabla)} "
            f"WHERE {q(columna)} > :fecha_corte"
        )
        encontradas = _contar(
            engine, sql, {"fecha_corte": FECHA_CORTE_CALIDAD.isoformat()}
        )
        detalle = f"fechas posteriores a {FECHA_CORTE_CALIDAD.isoformat()}"
    elif tipo == "formato_invalido":
        if columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": f"La columna '{columna}' no existe."}
        if columna.casefold() == "correo":
            if engine.dialect.name.startswith("postgres"):
                condicion = f"POSITION('@' IN {q(columna)}) = 0"
            else:
                condicion = f"INSTR({q(columna)}, '@') = 0"
            sql = (
                f"SELECT COUNT(*) FROM {q(tabla)} "
                f"WHERE {q(columna)} IS NOT NULL AND {condicion}"
            )
            detalle = "correos sin arroba"
        elif columna.casefold() == "curp":
            sql = (
                f"SELECT COUNT(*) FROM {q(tabla)} "
                f"WHERE {q(columna)} IS NOT NULL "
                f"AND LENGTH({q(columna)}) <> 18"
            )
            detalle = "CURP con longitud distinta de 18"
        else:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": "Formato no implementado para esta columna."}
    elif tipo == "valor_negativo":
        if columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": f"La columna '{columna}' no existe."}
        sql = f"SELECT COUNT(*) FROM {q(tabla)} WHERE {q(columna)} < 0"
    elif tipo == "valor_no_positivo":
        if columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": f"La columna '{columna}' no existe."}
        sql = f"SELECT COUNT(*) FROM {q(tabla)} WHERE {q(columna)} <= 0"
    elif tipo == "valor_no_catalogo":
        catalogo = {
            "cve_producto": ("dim_producto", "cve_producto"),
            "cve_txn": ("dim_txn", "cve_txn"),
        }.get(columna.casefold())
        if not catalogo or columna not in columnas:
            return {**hallazgo, "encontradas": None, "coincide": False,
                    "detalle": "No hay catálogo declarado para esta columna."}
        tabla_catalogo, columna_catalogo = catalogo
        sql = (
            f"SELECT COUNT(*) FROM {q(tabla)} t "
            f"WHERE NOT EXISTS (SELECT 1 FROM {q(tabla_catalogo)} c "
            f"WHERE c.{q(columna_catalogo)} = t.{q(columna)})"
        )
        detalle = f"valores fuera de {tabla_catalogo}"
    else:
        return {**hallazgo, "encontradas": None, "coincide": False,
                "detalle": f"Tipo de defecto no soportado: {tipo}"}

    if encontradas is None:
        encontradas = _contar(engine, sql)
    esperadas = int(hallazgo.get("filas_afectadas") or 0)
    return {
        **hallazgo,
        "encontradas": encontradas,
        "coincide": encontradas == esperadas,
        "detalle": detalle,
    }


def _contar(engine, sql: str, parametros: dict | None = None) -> int:
    with engine.connect() as conexion:
        return int(conexion.execute(text(sql), parametros or {}).scalar() or 0)


@tool
def validar_calidad_datos(tabla: str = "") -> str:
    """Detecta faltantes, duplicados y claves repetidas en una tabla o vista.

    Args:
        tabla: Tabla o vista a validar. Vacío usa raw_clientes.
    """
    try:
        engine, inspector, nombre, tablas, vistas, disponibles, error = _resolver_tabla(tabla)
        if error:
            return json.dumps(
                {"error": error, "catalogo_tablas": disponibles},
                ensure_ascii=False,
            )

        q = engine.dialect.identifier_preparer.quote
        columnas = [columna["name"] for columna in inspector.get_columns(nombre)]
        esperados = _esperados_calidad(engine, nombre)
        clave, origen_clave = _clave_tabla(inspector, nombre, esperados)
        faltantes: dict[str, int] = {}
        total = 0

        with engine.connect() as conexion:
            total = int(
                conexion.exec_driver_sql(f"SELECT COUNT(*) FROM {q(nombre)}").scalar()
                or 0
            )
            for columna in columnas:
                faltantes[columna] = int(
                    conexion.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {q(nombre)} "
                        f"WHERE {q(columna)} IS NULL"
                    ).scalar()
                    or 0
                )

            if columnas:
                condicion = " OR ".join(
                    f"{q(columna)} IS NULL" for columna in columnas
                )
                filas_con_faltante = int(
                    conexion.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {q(nombre)} WHERE {condicion}"
                    ).scalar()
                    or 0
                )
            else:
                filas_con_faltante = 0

            distintas = int(
                conexion.exec_driver_sql(
                    f"SELECT COUNT(*) FROM "
                    f"(SELECT DISTINCT * FROM {q(nombre)}) AS distintas"
                ).scalar()
                or 0
            )

        return json.dumps(
            {
                "base": DEFAULT_DB_NAME,
                "motor": engine.dialect.name,
                "tabla": nombre,
                "es_vista": nombre in vistas,
                "filas": total,
                "filas_con_algun_valor_faltante": filas_con_faltante,
                "valores_faltantes_por_columna": {
                    columna: cantidad
                    for columna, cantidad in faltantes.items()
                    if cantidad > 0
                },
                "filas_duplicadas_exactas": max(0, total - distintas),
                "clave_usada": clave,
                "origen_clave": origen_clave,
                "filas_sobrantes_por_clave": (
                    _sobrantes_por_clave(engine, nombre, clave)
                    if clave
                    else None
                ),
                "nota": (
                    "Un duplicado o una clave repetida es un hallazgo para "
                    "revisar; no confirma por sí solo que deban eliminarse "
                    "registros. La clave usada es la declarada (PK, restricción "
                    "única o hallazgo esperado), no una heurística por nombre."
                ),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as error:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(error).__name__}: {error}"},
            ensure_ascii=False,
        )


@tool
def evaluar_calidad(tabla: str = "") -> str:
    """Compara una tabla contra los hallazgos esperados declarados.

    Args:
        tabla: Tabla o vista a evaluar. Vacío usa raw_clientes.
    """
    try:
        engine, inspector, nombre, tablas, vistas, disponibles, error = _resolver_tabla(tabla)
        if error:
            return json.dumps(
                {"error": error, "catalogo_tablas": disponibles},
                ensure_ascii=False,
            )

        columnas = [columna["name"] for columna in inspector.get_columns(nombre)]
        esperados = _esperados_calidad(engine, nombre)
        evaluaciones = [
            _evaluar_hallazgo(engine, nombre, hallazgo, columnas)
            for hallazgo in esperados
        ]
        coinciden = sum(1 for evaluacion in evaluaciones if evaluacion["coincide"])
        return json.dumps(
            {
                "base": DEFAULT_DB_NAME,
                "motor": engine.dialect.name,
                "tabla": nombre,
                "es_vista": nombre in vistas,
                "evaluaciones": evaluaciones,
                "resumen": {
                    "esperados": len(evaluaciones),
                    "coinciden": coinciden,
                    "diferencias": len(evaluaciones) - coinciden,
                },
                "nota": (
                    "La comparación es determinista contra "
                    "calidad_hallazgos_esperados. Una diferencia es un hallazgo "
                    "para revisar, no una autorización para modificar datos."
                ),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as error:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(error).__name__}: {error}"},
            ensure_ascii=False,
        )


_HERRAMIENTAS_METADATA = {
    "inspeccionar_dataset",
    "validar_calidad_datos",
    "evaluar_calidad",
}


def _resultados_herramientas(mensajes) -> list[dict]:
    """Recupera las salidas JSON de las tools de metadatos."""
    pendientes: dict[str, str] = {}
    resultados: list[dict] = []
    vistos: set[tuple[str, str]] = set()

    for mensaje in mensajes:
        for llamada in getattr(mensaje, "tool_calls", None) or []:
            nombre = llamada.get("name")
            if nombre in _HERRAMIENTAS_METADATA:
                pendientes[llamada.get("id") or ""] = nombre

        nombre = getattr(mensaje, "name", None)
        if nombre not in _HERRAMIENTAS_METADATA:
            continue

        contenido = getattr(mensaje, "content", None)
        if isinstance(contenido, dict):
            datos = contenido
        else:
            try:
                datos = json.loads(contenido)
            except (TypeError, ValueError):
                continue
        if not isinstance(datos, dict):
            continue

        herramienta = pendientes.get(
            getattr(mensaje, "tool_call_id", "") or "",
            nombre,
        )
        tabla = str(datos.get("tabla") or datos.get("error") or "")
        clave = (herramienta, tabla)
        if clave in vistos:
            continue
        vistos.add(clave)
        resultados.append({"herramienta": herramienta, "datos": datos})

    return resultados


def _formatear_error(nombre: str, datos: dict) -> str:
    lineas = [
        f"No se pudo completar `{nombre}`: {datos.get('error', 'error desconocido')}"
    ]
    catalogo = datos.get("catalogo_tablas") or []
    if catalogo:
        lineas.append(
            "Tablas/vistas disponibles: "
            + ", ".join(f"`{tabla}`" for tabla in catalogo)
        )
    return "\n".join(lineas)


def _formatear_inspeccion(datos: dict) -> str:
    if "error" in datos:
        return _formatear_error("inspeccionar_dataset", datos)

    tabla = datos.get("tabla", "?")
    es_vista = "vista" if datos.get("es_vista") else "tabla"
    columnas = datos.get("columnas") or []
    filas = int(datos.get("filas") or 0)

    lineas = [
        f"**{tabla}** ({es_vista})",
        f"- Filas: {filas:,}",
        f"- Columnas: {len(columnas)}",
        "",
        "| Columna | Tipo | Valores faltantes |",
        "|---|---|---:|",
    ]
    for columna in columnas:
        lineas.append(
            "| `{}` | {} | {:,} |".format(
                columna.get("nombre", "?"),
                columna.get("tipo", "?"),
                int(columna.get("valores_faltantes") or 0),
            )
        )

    relaciones = datos.get("relaciones") or []
    lineas.append("")
    if relaciones:
        lineas.append("**Relaciones (foreign keys):**")
        for relacion in relaciones:
            columnas_fk = ", ".join(relacion.get("columnas") or [])
            lineas.append(
                f"- `{columnas_fk}` → `{relacion.get('referencia', '?')}`"
            )
    else:
        lineas.append("**Relaciones (foreign keys):** ninguna declarada.")

    catalogo = datos.get("catalogo_tablas") or []
    if catalogo:
        lineas.append("")
        lineas.append(
            "**Catálogo de tablas/vistas disponibles** "
            "(no son relaciones): "
            + ", ".join(f"`{tabla}`" for tabla in catalogo)
        )

    return "\n".join(lineas)


def _formatear_calidad(datos: dict) -> str:
    if "error" in datos:
        return _formatear_error("validar_calidad_datos", datos)

    tabla = datos.get("tabla", "?")
    filas = int(datos.get("filas") or 0)
    faltantes = datos.get("valores_faltantes_por_columna") or {}
    filas_con_faltante = int(
        datos.get("filas_con_algun_valor_faltante") or 0
    )
    duplicadas = int(datos.get("filas_duplicadas_exactas") or 0)
    sobrantes = datos.get("filas_sobrantes_por_clave")

    lineas = [
        f"**Calidad de `{tabla}`:**",
        f"- Filas: {filas:,}",
        f"- Filas con algún valor faltante: {filas_con_faltante:,}",
    ]

    if faltantes:
        lineas.extend([
            "",
            "| Columna | Valores faltantes |",
            "|---|---:|",
        ])
        for columna, cantidad in faltantes.items():
            lineas.append(f"| `{columna}` | {int(cantidad):,} |")
    else:
        lineas.append("- Valores faltantes por columna: ninguno.")

    clave = datos.get("clave_usada") or []
    origen = datos.get("origen_clave") or ""
    if clave:
        lineas.append(
            f"- Clave usada: {', '.join(f'`{columna}`' for columna in clave)} "
            f"({origen})."
        )
    else:
        lineas.append("- Clave usada: no hay PK, restricción única ni hallazgo esperado.")

    lineas.append(f"- Filas duplicadas exactas: {duplicadas:,}")
    if sobrantes is None:
        lineas.append(
            "- Filas sobrantes por clave: no aplica (sin clave declarada)."
        )
    else:
        lineas.append(f"- Filas sobrantes por clave: {int(sobrantes):,}")

    nota = datos.get("nota")
    if nota:
        lineas.append(f"- Nota: {nota}")

    return "\n".join(lineas)


def _formatear_evaluacion_calidad(datos: dict) -> str:
    if "error" in datos:
        return _formatear_error("evaluar_calidad", datos)

    tabla = datos.get("tabla", "?")
    evaluaciones = datos.get("evaluaciones") or []
    resumen = datos.get("resumen") or {}
    lineas = [
        f"**Evaluación de calidad de `{tabla}` contra hallazgos esperados:**",
        f"- Esperados: {int(resumen.get('esperados') or 0):,}",
        f"- Coinciden: {int(resumen.get('coinciden') or 0):,}",
        f"- Diferencias: {int(resumen.get('diferencias') or 0):,}",
    ]
    if evaluaciones:
        lineas.extend([
            "",
            "| ID | Defecto | Columna | Esperadas | Encontradas | Estado |",
            "|---:|---|---|---:|---:|---|",
        ])
        for evaluacion in evaluaciones:
            estado = "coincide" if evaluacion.get("coincide") else "diferencia"
            encontradas = evaluacion.get("encontradas")
            lineas.append(
                "| {} | {} | `{}` | {:,} | {} | {} |".format(
                    evaluacion.get("id", "?"),
                    evaluacion.get("tipo_defecto", "?"),
                    evaluacion.get("columna", "?"),
                    int(evaluacion.get("filas_afectadas") or 0),
                    "?" if encontradas is None else f"{int(encontradas):,}",
                    estado,
                )
            )
    else:
        lineas.append("- No hay hallazgos esperados declarados para esta tabla.")
    return "\n".join(lineas)


def formatear_resultados_verificados(resultados: list[dict]) -> str:
    """Convierte salidas de tools en una respuesta determinista y verificable."""
    if not resultados:
        return ""

    partes = ["Respuesta verificada con las herramientas de metadatos:"]
    for resultado in resultados:
        nombre = resultado.get("herramienta")
        datos = resultado.get("datos") or {}
        if nombre == "inspeccionar_dataset":
            partes.append(_formatear_inspeccion(datos))
        elif nombre == "validar_calidad_datos":
            partes.append(_formatear_calidad(datos))
        elif nombre == "evaluar_calidad":
            partes.append(_formatear_evaluacion_calidad(datos))
        else:
            partes.append(f"Resultado de `{nombre}`: {json.dumps(datos, ensure_ascii=False)}")

    partes.append("No se exponen valores individuales de clientes.")
    return "\n\n".join(partes)


class DataStewardAgent:
    def __init__(self):
        self.nombre = "data_steward"
        self.descripcion = (
            "Revisa estructura y calidad de la base sintética: tablas, columnas, "
            "faltantes, duplicados y claves repetidas, sin revelar registros."
        )
        self.modelo = modelo_de("data_steward")
        self._tools = [
            inspeccionar_dataset,
            validar_calidad_datos,
            evaluar_calidad,
        ]
        self.herramientas = [herramienta.name for herramienta in self._tools]
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agent = create_agent(
                model=llm,
                tools=self._tools,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agent

    def formatear_resultados_verificados(self, resultados: list[dict]) -> str:
        return formatear_resultados_verificados(resultados)

    def procesar(self, consulta: str) -> str:
        if _solicita_registros_personales(consulta):
            return (
                "No puedo mostrar ni inferir datos personales de clientes "
                "ni valores de registros individuales. Sí puedo ayudar con "
                "estructura, faltantes, duplicados y otros agregados de la "
                "base sintética."
            )

        resultado = self._get_agent().invoke(
            {"messages": [{"role": "user", "content": consulta}]}
        )
        mensajes = resultado["messages"]
        verificados = _resultados_herramientas(mensajes)
        if verificados:
            if _SOLICITA_CALIDAD.search(consulta):
                tablas = [
                    item["datos"].get("tabla")
                    for item in verificados
                    if item["herramienta"] == "inspeccionar_dataset"
                    and "error" not in item["datos"]
                    and item["datos"].get("tabla")
                ]
                con_validacion = {
                    item["datos"].get("tabla")
                    for item in verificados
                    if item["herramienta"] == "validar_calidad_datos"
                }
                con_evaluacion = {
                    item["datos"].get("tabla")
                    for item in verificados
                    if item["herramienta"] == "evaluar_calidad"
                }
                for tabla in dict.fromkeys(tablas):
                    if tabla not in con_validacion:
                        try:
                            calidad = json.loads(
                                validar_calidad_datos.invoke({"tabla": tabla})
                            )
                        except Exception:  # noqa: BLE001 - se conserva la inspección
                            calidad = None
                        if calidad is not None:
                            verificados.append({
                                "herramienta": "validar_calidad_datos",
                                "datos": calidad,
                            })
                    if tabla not in con_evaluacion:
                        try:
                            evaluacion = json.loads(
                                evaluar_calidad.invoke({"tabla": tabla})
                            )
                        except Exception:  # noqa: BLE001 - se conserva la inspección
                            evaluacion = None
                        if evaluacion and evaluacion.get("evaluaciones"):
                            verificados.append({
                                "herramienta": "evaluar_calidad",
                                "datos": evaluacion,
                            })
            return formatear_resultados_verificados(verificados)

        mensaje = mensajes[-1]
        if isinstance(mensaje.content, str):
            respuesta = mensaje.content
        else:
            respuesta = "".join(
                bloque.get("text", "")
                for bloque in mensaje.content
                if isinstance(bloque, dict) and bloque.get("type") == "text"
            )

        if _parece_respuesta_sin_herramienta(respuesta):
            return _MENSAJE_SIN_HERRAMIENTA
        return respuesta

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_steward_agent = DataStewardAgent()
