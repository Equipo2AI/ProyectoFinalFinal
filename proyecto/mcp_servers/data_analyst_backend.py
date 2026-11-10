"""Backend SQL de Data Analyst; conserva las herramientas y controles del equipo."""

import json
import os
import re
import unicodedata

from dotenv import load_dotenv
from langchain_core.tools import tool
from sqlalchemy import create_engine, event, inspect

from proyecto.config.models import modelo_de

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
# El superusuario puede usar la tabla limpia de personas para auditoría; las
# tablas crudas siguen fuera de su alcance.
_TABLAS_EXCLUIDAS_SUPERUSUARIO = re.compile(
    r"\b(?:"
    + "|".join(sorted(TABLAS_EXCLUIDAS - {"dim_cliente_persona"}))
    + r")\b",
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
- No muestres datos personales de clientes (nombres, correos, teléfonos,
  CURP, RFC); trabaja con resultados agregados.
- No modifiques datos ni afirmes haberlo hecho.
- Si la pregunta es ambigua (no dice qué métrica, periodo o tabla), pide
  aclaración o explica el supuesto que usaste.
- Si la base no tiene la información pedida, dilo explícitamente.
"""

_REGLAS_PII = (
    "- No muestres datos personales de clientes (nombres, correos, teléfonos,\n"
    "  CURP, RFC); trabaja con resultados agregados."
)


def _normalizar_perfil(perfil: dict | None) -> tuple[str, int | None]:
    perfil = perfil or {}
    rol = str(perfil.get("rol") or "").strip().lower()
    cliente_id = perfil.get("cliente_id")
    try:
        cliente_id = int(cliente_id) if cliente_id is not None else None
    except (TypeError, ValueError):
        cliente_id = None
    return rol, cliente_id


def _contexto_sesion(rol: str, cliente_id: int | None) -> str:
    if rol == "usuario" and cliente_id is not None:
        return (
            f"Sesión autenticada: rol=usuario, cliente_id={cliente_id}. "
            f"El usuario solo puede consultar sus propios datos; filtra por "
            f"num_cte = {cliente_id}. No consultes ni muestres cuentas o "
            "clientes de terceros."
        )
    if rol == "superusuario":
        return (
            "Sesión autenticada: rol=superusuario. El usuario es personal "
            "autorizado para auditoría y reportes; puede consultar agregados, "
            "cuentas de terceros y datos personales cuando la pregunta lo "
            "requiera. No se niegue por privacidad; indique el propósito de "
            "auditoría y cite la tabla o vista usada."
        )
    return ""


def _prompt_sesion(rol: str, cliente_id: int | None) -> str:
    if rol == "usuario" and cliente_id is not None:
        return (
            SYSTEM_PROMPT
            + "\n\nSesión autenticada de usuario normal:\n"
            f"- El cliente autenticado es num_cte = {cliente_id}.\n"
            f"- Toda consulta de detalle debe filtrar por num_cte = {cliente_id}.\n"
            "- Nunca consultes ni muestres datos de otras cuentas o clientes.\n"
            "- Si la pregunta pide datos de otro cliente o cuenta, niégate y "
            "explica que solo puedes ver los datos del cliente autenticado.\n"
        )
    if rol == "superusuario":
        base = SYSTEM_PROMPT.replace(
            _REGLAS_PII,
            "- Como superusuario autorizado, puedes mostrar datos personales "
            "de clientes (nombre, RFC, etc.) cuando la pregunta de auditoría lo "
            "requiera.",
        )
        return (
            base
            + "\n\nSesión autorizada de superusuario:\n"
            "- El usuario es personal autorizado para auditoría y reportes.\n"
            "- Puede consultar datos de cualquier cliente y mostrar nombre, RFC "
            "u otros datos personales cuando la pregunta lo requiera.\n"
            "- No se niegue por privacidad; indique que es una consulta de "
            "auditoría y cite la tabla o vista usada.\n"
        )
    return SYSTEM_PROMPT


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _validar_consulta(sql: str, permitir_pii: bool = False) -> str | None:
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
    patron = _TABLAS_EXCLUIDAS_SUPERUSUARIO if permitir_pii else _TABLAS_EXCLUIDAS
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
        self._agentes: dict[str, object] = {}
        self._rol_actual = "usuario"
        self._cliente_actual: int | None = None
        self._tools = self._crear_herramientas()
        self.herramientas = [t.name for t in self._tools]

    def conectar(self, url_db: str) -> None:
        """Point the agent to another database without recreating it."""
        if self._engine is not None:
            self._engine.dispose()
        self.url_db = url_db
        self._engine = None
        self._agentes = {}

    def _get_agente(self, rol: str = "", cliente_id: int | None = None):
        clave = f"{rol}:{cliente_id}" if rol else "normal"
        if clave not in self._agentes:
            from langchain.agents import create_agent
            from langchain_ollama import ChatOllama

            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agentes[clave] = create_agent(
                model=llm,
                tools=self._tools,
                system_prompt=_prompt_sesion(rol, cliente_id),
            )
        return self._agentes[clave]

    def procesar(
        self,
        consulta: str,
        contexto: str = "",
        perfil: dict | None = None,
    ) -> str:
        rol, cliente_id = _normalizar_perfil(perfil)
        self._rol_actual = rol
        self._cliente_actual = cliente_id
        sesion = _contexto_sesion(rol, cliente_id)
        if sesion:
            contexto = f"{contexto}\n\n{sesion}".strip()
        contenido = consulta
        if contexto.strip():
            contenido = (
                f"Contexto de la conversación:\n{contexto}\n\nPregunta: {consulta}"
            )
        resultado = self._get_agente(rol, cliente_id).invoke(
            {"messages": [{"role": "user", "content": contenido}]},
            config={"recursion_limit": 25},
        )
        return resultado["messages"][-1].content

    def _get_engine(self):
        if self._engine is None:
            if not self.url_db:
                raise RuntimeError(
                    "No hay base configurada: define DATABASE_URL o usa conectar()."
                )
            connect_args = {}
            if self.url_db.startswith("postgresql"):
                connect_args["options"] = "-c default_transaction_read_only=on"
            self._engine = create_engine(self.url_db, connect_args=connect_args)
            if self._engine.dialect.name == "sqlite":
                event.listen(self._engine, "connect", _sqlite_solo_lectura)
        return self._engine

    def _objetos_disponibles(
        self, inspector, permitir_pii: bool = False
    ) -> tuple[list[str], list[str]]:
        """Return the tables and views the analyst may use."""
        excluidas = TABLAS_EXCLUIDAS - (
            {"dim_cliente_persona"} if permitir_pii else set()
        )
        tablas = [t for t in inspector.get_table_names() if t not in excluidas]
        vistas = [v for v in inspector.get_view_names() if v not in excluidas]
        return tablas, vistas

    def _crear_herramientas(self):
        @tool
        def listar_tablas() -> str:
            """Lista las tablas y vistas de la base de datos y el motor SQL (sqlite, postgresql...)."""
            try:
                engine = self._get_engine()
                tablas, vistas = self._objetos_disponibles(
                    inspect(engine), permitir_pii=self._rol_actual == "superusuario"
                )
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
                tablas, vistas = self._objetos_disponibles(
                    inspector, permitir_pii=self._rol_actual == "superusuario"
                )
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
            if (
                _normalizar(columna) in COLUMNAS_SENSIBLES
                and self._rol_actual != "superusuario"
            ):
                return _json({"error": "Columna con datos personales: no se muestran valores."})
            try:
                engine = self._get_engine()
                inspector = inspect(engine)
                tablas, vistas = self._objetos_disponibles(
                    inspector, permitir_pii=self._rol_actual == "superusuario"
                )
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
            problema = _validar_consulta(
                sql, permitir_pii=self._rol_actual == "superusuario"
            )
            if problema:
                return _json({"error": problema})

            try:
                with self._get_engine().connect() as conexion:
                    resultado = conexion.exec_driver_sql(sql.strip().rstrip(";"))
                    columnas = list(resultado.keys())
                    filas = resultado.fetchmany(MAX_FILAS + 1)
            except Exception as error:
                # El mensaje del motor le sirve al LLM para corregir su SQL.
                return _json({"error": f"{type(error).__name__}: {str(error).splitlines()[0]}"})

            sensibles = [c for c in columnas if _normalizar(c) in COLUMNAS_SENSIBLES]
            if sensibles and self._rol_actual != "superusuario":
                return _json({
                    "error": (
                        f"La consulta devuelve datos personales ({', '.join(sensibles)}). "
                        "Usa agregados (COUNT, AVG...) en su lugar."
                    )
                })

            return _json({
                "columnas": columnas,
                "filas": [list(fila) for fila in filas[:MAX_FILAS]],
                "truncado": len(filas) > MAX_FILAS,
            })

        return [listar_tablas, describir_tabla, valores_columna, ejecutar_consulta]
