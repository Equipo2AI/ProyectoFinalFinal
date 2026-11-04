"""Data Steward over the synthetic Banamex database.

Este agente revisa la estructura y la calidad de la base sintética (tablas,
columnas, faltantes y duplicados) y solo devuelve agregados; nunca valores de
registros individuales. El CSV de clientes fue retirado del flujo.
"""

from __future__ import annotations

import json
import re
import unicodedata

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from sqlalchemy import inspect

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
                {"error": error, "tablas_disponibles": disponibles},
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
                "tablas_disponibles": disponibles,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as error:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(error).__name__}: {error}"},
            ensure_ascii=False,
        )


def _columnas_duplicadas(engine, tabla: str, columnas: list[str]) -> int | None:
    """Cuenta filas sobrantes de una clave; None si no hay clave razonable."""
    nombres = {columna.casefold() for columna in columnas}
    clave: list[str] = []
    if "num_cte" in nombres:
        clave = ["num_cte"]
    elif "num_cta" in nombres:
        clave = ["num_cta"]
    elif len(columnas) == 1:
        clave = [columnas[0]]

    if not clave:
        return None

    q = engine.dialect.identifier_preparer.quote
    grupo = ", ".join(q(columna) for columna in clave)
    sql = (
        "SELECT COALESCE(SUM(n - 1), 0) FROM ("
        f"SELECT {grupo}, COUNT(*) AS n FROM {q(tabla)} "
        f"GROUP BY {grupo} HAVING COUNT(*) > 1"
        ")"
    )
    with engine.connect() as conexion:
        return int(conexion.exec_driver_sql(sql).scalar() or 0)


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
                {"error": error, "tablas_disponibles": disponibles},
                ensure_ascii=False,
            )

        q = engine.dialect.identifier_preparer.quote
        columnas = [columna["name"] for columna in inspector.get_columns(nombre)]
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
                    f"SELECT COUNT(*) FROM (SELECT DISTINCT * FROM {q(nombre)})"
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
                "filas_sobrantes_por_clave": _columnas_duplicadas(
                    engine, nombre, columnas
                ),
                "nota": (
                    "Un duplicado o una clave repetida es un hallazgo para "
                    "revisar; no confirma por sí solo que deban eliminarse "
                    "registros."
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


class DataStewardAgent:
    def __init__(self):
        self.nombre = "data_steward"
        self.descripcion = (
            "Revisa estructura y calidad de la base sintética: tablas, columnas, "
            "faltantes, duplicados y claves repetidas, sin revelar registros."
        )
        self.modelo = "llama3.1:latest"
        self._tools = [inspeccionar_dataset, validar_calidad_datos]
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
        mensaje = resultado["messages"][-1]
        if isinstance(mensaje.content, str):
            return mensaje.content
        return "".join(
            bloque.get("text", "")
            for bloque in mensaje.content
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_steward_agent = DataStewardAgent()
