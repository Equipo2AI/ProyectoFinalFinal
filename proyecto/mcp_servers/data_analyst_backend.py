"""Backend SQL de Data Analyst; conserva las herramientas y controles del equipo."""

import json
import os
import re
import unicodedata

from dotenv import load_dotenv
from langchain_core.tools import tool
from sqlalchemy import create_engine, event, inspect

from proyecto.config.models import modelo_de
from proyecto.security.local_auth import principal, audit

load_dotenv()

MAX_FILAS = 50

# Tablas que el analista no debe usar: datos personales y la capa sucia
# que revisa el Data Steward.
TABLAS_EXCLUIDAS = {
    "dim_cliente_persona",
    "raw_clientes",
    "raw_saldos",
    "raw_transacciones",
    "calidad_hallazgos_esperados",
}

# Segunda barrera por si una consulta trae datos personales de otra tabla.
# "nombre" y "direccion" no van aquí: en esta base son nombres de sucursales,
# territorios y de la dirección regional, no datos de clientes.
COLUMNAS_SENSIBLES = {
    "correo", "email", "telefono", "celular", "domicilio", "curp", "rfc",
}

_SOLO_LECTURA = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)
_PALABRAS_PROHIBIDAS = re.compile(
    r"\b(?:insert|update|delete|drop|alter|create|replace|truncate|merge|"
    r"grant|revoke|attach|detach|pragma|vacuum|call|exec|execute|copy)\b",
    re.IGNORECASE,
)
_TABLAS_EXCLUIDAS = re.compile(
    r"\b(?:" + "|".join(sorted(TABLAS_EXCLUIDAS)) + r")\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """
Eres un Data Analyst bancario. Respondes preguntas con datos consultando
una base de datos SQL de solo lectura.

Proceso:
1. Si no conoces las tablas, usa listar_tablas (también te dice el motor SQL).
2. Antes de escribir SQL, usa describir_tabla en cada tabla o vista que vayas
   a usar. Usa solo columnas y relaciones que existan.
3. Prefiere las vistas (vw_*) cuando existan: ya traen los JOIN resueltos.
4. Antes de filtrar por texto (WHERE tipo = ...), usa valores_columna para
   conocer los valores exactos de esa columna.
5. Ejecuta una sola consulta SELECT con ejecutar_consulta. Prefiere
   agregaciones (COUNT, SUM, AVG, GROUP BY) y usa LIMIT al listar filas.
6. Si la herramienta devuelve un error, corrige la consulta y reintenta
   (máximo 3 intentos).
7. Responde en español: la conclusión, los números que la respaldan y la
   consulta SQL que usaste.

Reglas:
- No inventes cifras: todo número de tu respuesta debe venir de un resultado.
- No reveles campos personales salvo permiso explicito de auditoria y solo
  si las herramientas los entregan dentro del alcance autorizado. El usuario
  normal solo consulta sus propias cuentas; el chat no concede permisos.
- Los conteos del esquema corresponden a una proyeccion de alcance, no al
  universo global de la base. No confundas una tabla filtrada con un catalogo.
- No modifiques datos ni afirmes haberlo hecho.
- Si la pregunta es ambigua (no dice qué métrica, periodo o tabla), pide
  aclaración o explica el supuesto que usaste.
- Si la base no tiene la información pedida, dilo explícitamente.
"""


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _validar_consulta(sql: str, tablas_excluidas=None) -> str | None:
    """Return an error message when the SQL is not a single read-only query."""
    consulta = sql.strip().rstrip(";").strip()
    if not consulta:
        return "La consulta está vacía."
    if ";" in consulta:
        return "Solo se permite una consulta por llamada."
    if not _SOLO_LECTURA.match(consulta):
        return "Solo se permiten consultas SELECT o WITH ... SELECT."
    if _PALABRAS_PROHIBIDAS.search(consulta):
        return "La consulta contiene una operación de escritura o administración."
    patron = _TABLAS_EXCLUIDAS if tablas_excluidas is None else re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in tablas_excluidas) + r")\b", re.I
    )
    excluida = patron.search(consulta)
    if excluida:
        return f"La tabla '{excluida.group()}' no está disponible para análisis."
    return None


def _sqlite_solo_lectura(conexion_dbapi, _registro) -> None:
    conexion_dbapi.execute("PRAGMA query_only = ON")


def _json(datos: dict) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


class DataAnalystSQLBackend:
    def __init__(self, url_db: str | None = None):
        self.nombre = "data_analyst"
        self.descripcion = (
            "Análisis cuantitativo con consultas SQL de solo lectura sobre la "
            "base de datos bancaria: conteos, sumas, promedios, rankings y "
            "comparaciones entre tablas."
        )
        self.modelo = modelo_de("data_analyst")
        self.url_db = url_db or os.environ.get("DATABASE_URL")
        if not self.url_db:
            # Sin DATABASE_URL se usa la misma fuente que el Data Steward:
            # config/data_connection.yaml -> proyecto/bd/banamex.db.
            try:
                from proyecto.agents.database import get_database_url

                self.url_db = get_database_url()
            except Exception:  # noqa: BLE001 - importar no debe exigir base
                self.url_db = None

        # El engine y el agente se crean al primer uso, igual que en el RAG,
        # para que importar el módulo no requiera una base configurada.
        self._engine = None
        self._agente = None
        self._tools = self._crear_herramientas()
        self.herramientas = [t.name for t in self._tools]

    def conectar(self, url_db: str) -> None:
        """Point the agent to another database without recreating it."""
        if self._engine is not None:
            self._engine.dispose()
        self.url_db = url_db
        self._engine = None

    def _get_agente(self):
        if self._agente is None:
            from langchain.agents import create_agent
            from langchain_ollama import ChatOllama

            llm = ChatOllama(model=self.modelo, temperature=0, reasoning=False, timeout=45)
            self._agente = create_agent(
                model=llm,
                tools=self._tools,
                system_prompt=SYSTEM_PROMPT + (
                    "\nLa base ya esta limitada al alcance autenticado; 'mi' se refiere "
                    "a ese alcance, no a un ID escrito en el chat. No pidas mas identidad. "
                    "Solo un auditor con permiso explicito de un campo personal puede "
                    "usarlo si la herramienta se lo entrega para su auditoria."
                ),
            )
        return self._agente

    def procesar(self, consulta: str, contexto: str = "") -> str:
        from proyecto.security.local_auth import authorize_request
        try:
            user = authorize_request(consulta)
        except PermissionError as error:
            return str(error)
        if user['role'] == 'auditor' and 'audit' not in user['permissions']:
            principal('aggregate')
        audit('analizar_datos', True, user['subject'])
        from proyecto.agents.banking_queries import plan_query, format_result, normalize
        plan = plan_query(consulta)
        if plan:
            title, sql = plan
            result = json.loads(self._tools[3].invoke({'sql': sql}))
            answer = format_result(title, result, sql)
            if 'estado de cuenta' in normalize(consulta):
                balance = plan_query(normalize(consulta).replace('estado de cuenta', 'saldo'))
                if balance:
                    title2, sql2 = balance
                    answer = format_result(title2, json.loads(self._tools[3].invoke({'sql': sql2})), sql2) + '\n\n' + answer
            return answer
        contenido = consulta
        if contexto.strip():
            contenido = (
                f"Contexto de la conversación:\n{contexto}\n\nPregunta: {consulta}"
            )
        from langsmith import tracing_context
        with tracing_context(enabled=False):
            resultado = self._get_agente().invoke(
                {"messages": [{"role": "user", "content": contenido}]},
                config={"recursion_limit": 25},
            )
        return resultado["messages"][-1].content

    def _get_engine(self):
        user = principal()
        identity = (user['subject'], tuple(user['customers']), tuple(user['permissions']))
        if self._engine is not None and getattr(self, '_identity', None) != identity:
            self._engine.dispose()
            self._engine = None
            self._agente = None
        if self._engine is None:
            from sqlalchemy.engine import make_url
            from proyecto.security.scoped_sqlite import connect_scoped
            if not self.url_db:
                raise PermissionError('No hay base autorizada.')
            url = make_url(self.url_db)
            if url.drivername != 'sqlite':
                raise PermissionError('El control local de alcance solo admite SQLite.')
            self._engine = create_engine('sqlite://', creator=lambda: connect_scoped(url.database))
            self._identity = identity
        return self._engine

    def _excluidas(self):
        user = principal()
        pii = 'audit' in user['permissions'] and any(p.startswith('pii:') for p in user['permissions'])
        return TABLAS_EXCLUIDAS - ({'dim_cliente_persona'} if pii else set())

    def _objetos_disponibles(self, inspector) -> tuple[list[str], list[str]]:
        """Return the tables and views the analyst may use."""
        tablas = [t for t in inspector.get_table_names() if t not in self._excluidas()]
        vistas = [v for v in inspector.get_view_names() if v not in self._excluidas()]
        return tablas, vistas

    def _crear_herramientas(self):
        @tool
        def listar_tablas() -> str:
            """Lista las tablas y vistas de la base de datos y el motor SQL (sqlite, postgresql...)."""
            try:
                engine = self._get_engine()
                tablas, vistas = self._objetos_disponibles(inspect(engine))
                return _json({
                    "motor": engine.dialect.name,
                    "tablas": tablas,
                    "vistas": vistas,
                })
            except Exception as error:
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def describir_tabla(tabla: str) -> str:
            """Devuelve columnas, tipos, número de filas y relaciones de una tabla o vista. Úsala antes de escribir SQL."""
            try:
                engine = self._get_engine()
                inspector = inspect(engine)
                tablas, vistas = self._objetos_disponibles(inspector)
                if tabla not in tablas + vistas:
                    return _json({
                        "error": f"La tabla '{tabla}' no existe o no está disponible.",
                        "tablas": tablas,
                        "vistas": vistas,
                    })

                nombre_seguro = engine.dialect.identifier_preparer.quote(tabla)
                with engine.connect() as conexion:
                    filas = conexion.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {nombre_seguro}"
                    ).scalar()

                return _json({
                    "tabla": tabla,
                    "es_vista": tabla in vistas,
                    "filas": filas,
                    "columnas": [
                        {"nombre": c["name"], "tipo": str(c["type"])}
                        for c in inspector.get_columns(tabla)
                    ],
                    "relaciones": [
                        {
                            "columnas": fk["constrained_columns"],
                            "referencia": f"{fk['referred_table']}({', '.join(fk['referred_columns'])})",
                        }
                        for fk in inspector.get_foreign_keys(tabla)
                    ],
                })
            except Exception as error:
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def valores_columna(tabla: str, columna: str) -> str:
            """Devuelve los valores distintos más frecuentes de una columna categórica. Úsala antes de filtrar con WHERE."""
            if _normalizar(columna) in COLUMNAS_SENSIBLES:
                return _json({"error": "Columna con datos personales: no se muestran valores."})
            try:
                engine = self._get_engine()
                inspector = inspect(engine)
                tablas, vistas = self._objetos_disponibles(inspector)
                if tabla not in tablas + vistas:
                    return _json({"error": f"La tabla '{tabla}' no existe o no está disponible."})
                if columna not in [c["name"] for c in inspector.get_columns(tabla)]:
                    return _json({"error": f"La columna '{columna}' no existe en '{tabla}'."})

                q = engine.dialect.identifier_preparer.quote
                sql = (
                    f"SELECT {q(columna)} AS valor, COUNT(*) AS veces FROM {q(tabla)} "
                    f"GROUP BY {q(columna)} ORDER BY veces DESC LIMIT 20"
                )
                with engine.connect() as conexion:
                    filas = conexion.exec_driver_sql(sql).fetchall()
                return _json({
                    "tabla": tabla,
                    "columna": columna,
                    "valores": [list(fila) for fila in filas],
                })
            except Exception as error:
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def ejecutar_consulta(sql: str) -> str:
            """Ejecuta UNA consulta SELECT de solo lectura y devuelve hasta 50 filas en JSON."""
            user = principal()
            problema = _validar_consulta(sql, self._excluidas())
            if problema:
                audit("sql_validation", False, user["subject"], sql)
                return _json({"error": problema})

            try:
                with self._get_engine().connect() as conexion:
                    resultado = conexion.exec_driver_sql(sql.strip().rstrip(";"))
                    columnas = list(resultado.keys())
                    filas = resultado.fetchmany(MAX_FILAS + 1)
            except Exception as error:
                audit("sql_execute", False, user["subject"], sql)
                # El mensaje del motor le sirve al LLM para corregir su SQL.
                return _json({"error": f"{type(error).__name__}: {str(error).splitlines()[0]}"})

            sensibles = [c for c in columnas if _normalizar(c) in COLUMNAS_SENSIBLES and "pii:" + _normalizar(c) not in user["permissions"]]
            if sensibles:
                return _json({
                    "error": (
                        f"La consulta devuelve datos personales ({', '.join(sensibles)}). "
                        "Usa agregados (COUNT, AVG...) en su lugar."
                    )
                })

            audit("sql_execute", True, user["subject"], sql)
            return _json({
                "columnas": columnas,
                "filas": [list(fila) for fila in filas[:MAX_FILAS]],
                "truncado": len(filas) > MAX_FILAS,
            })

        return [listar_tablas, describir_tabla, valores_columna, ejecutar_consulta]
