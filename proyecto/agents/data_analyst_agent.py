"""Data Analyst: answers quantitative questions with read-only SQL queries."""

import json
import re
import unicodedata

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from sqlalchemy import inspect

from .database import (
    COLUMNAS_SENSIBLES,
    TABLAS_EXCLUIDAS,
    create_readonly_engine,
    get_database_url,
)

MAX_FILAS = 50

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
_CIFRAS = re.compile(r"\d")
_PIDE_HERRAMIENTA = re.compile(
    r"\b(?:listar_tablas|describir_tabla|valores_columna|ejecutar_consulta|"
    r"ejecutar\s+la\s+funci[oó]n|herramienta)\b",
    re.IGNORECASE,
)
_SIN_DATOS = re.compile(
    r"\b(?:no\s+(?:puedo|tengo|hay|existe|está|esta|se\s+encontr|cuento)|"
    r"sin\s+(?:datos|información)|no\s+(?:está|esta)\s+disponible|"
    r"no\s+disponible|no\s+contiene)\b",
    re.IGNORECASE,
)
_MENSAJE_SIN_SQL = (
    "No ejecuté una consulta SQL en este turno, así que no tengo cifras "
    "verificadas para responder. Para evitar inventar datos, no voy a dar "
    "números. Reformula la pregunta indicando la tabla o métrica que quieres "
    "consultar y lo intento de nuevo."
)

SYSTEM_PROMPT = """
Eres un Data Analyst bancario. Respondes preguntas con datos consultando
una base de datos SQL de solo lectura.

Proceso:
1. Si no conoces las tablas, usa listar_tablas (también te dice el motor SQL).
2. Antes de escribir SQL, usa describir_tabla en cada tabla o vista que vayas
   a usar. Usa solo columnas y relaciones que existan.
3. Prefiere las vistas (vw_*) cuando existan: ya traen los JOIN resueltos.
   Para clientes usa `vw_cliente_publico`; no uses `raw_clientes`, que contiene
   duplicados y datos personales.
4. Antes de filtrar por texto (WHERE tipo = ...), usa valores_columna para
   conocer los valores exactos de esa columna.
5. Ejecuta cada consulta SELECT que necesites con ejecutar_consulta; puedes
   llamarla varias veces en el mismo turno. Prefiere agregaciones (COUNT, SUM,
   AVG, GROUP BY) y usa LIMIT al listar filas.
6. Si la herramienta devuelve un error, corrige la consulta y reintenta
   (máximo 3 intentos).
7. Responde en español: la conclusión, los números que la respaldan y la
   consulta SQL que usaste.
8. No respondas con cifras si no ejecutaste al menos una consulta SQL en este
   turno. Escribir la consulta en el texto no cuenta: debes llamar a
   ejecutar_consulta y usar el resultado.
9. Si la pregunta es ambigua y no sabes qué métrica, periodo, filtro o tabla
   usar, pregunta al usuario antes de ejecutar. Si la intención es clara,
   ejecuta las consultas necesarias sin pedir permiso: son de solo lectura.
10. Tú tienes acceso a las herramientas. Nunca le pidas al usuario que
    ejecute listar_tablas, describir_tabla, valores_columna ni
    ejecutar_consulta: llámalas tú directamente.

Reglas:
- No inventes cifras: todo número de tu respuesta debe venir de un resultado.
- Si la pregunta combina un total con una distribución, responde ambos: incluye
  el total (COUNT(*)) y los grupos, no solo una de las dos partes.
- Si una columna pedida no aparece en describir_tabla, no la sustituyas por
  otra columna parecida; di que esa información no está disponible.
- No muestres datos personales de clientes (nombres, correos, teléfonos,
  CURP, RFC); trabaja con resultados agregados.
- Si el motor es SQLite y calculas edades, usa paréntesis:
  `(julianday('now') - julianday(fec_nac)) / 365.25`. Sin paréntesis el
  resultado es incorrecto.
- No modifiques datos ni afirmes haberlo hecho.
- Si la pregunta es ambigua (no dice qué métrica, periodo o tabla), pide
  aclaración o explica el supuesto que usaste.
- Si la base no tiene la información pedida, dilo explícitamente.
"""


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _validar_consulta(sql: str) -> str | None:
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
    excluida = _TABLAS_EXCLUIDAS.search(consulta)
    if excluida:
        return f"La tabla '{excluida.group()}' no está disponible para análisis."
    return None


def _json(datos: dict) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


def _consultas_ejecutadas(mensajes) -> list[dict]:
    """Recupera el SQL y los resultados reales de cada ejecutar_consulta."""
    pendientes: dict[str, str] = {}
    ejecutadas: list[dict] = []

    for mensaje in mensajes:
        for llamada in getattr(mensaje, "tool_calls", None) or []:
            if llamada.get("name") == "ejecutar_consulta":
                argumentos = llamada.get("args") or {}
                pendientes[llamada.get("id") or ""] = str(argumentos.get("sql") or "")

        if getattr(mensaje, "name", None) != "ejecutar_consulta":
            continue

        contenido = getattr(mensaje, "content", None)
        if isinstance(contenido, dict):
            datos = contenido
        else:
            try:
                datos = json.loads(contenido)
            except (TypeError, ValueError):
                continue
        if not isinstance(datos, dict) or "filas" not in datos or "error" in datos:
            continue

        ejecutadas.append({
            "sql": pendientes.get(getattr(mensaje, "tool_call_id", "") or "", ""),
            "columnas": datos.get("columnas") or [],
            "filas": datos.get("filas") or [],
            "truncado": bool(datos.get("truncado")),
        })

    return ejecutadas


def _normalizar_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip().rstrip(";").strip().casefold()


def _bloques_sql(texto: str) -> list[str]:
    return re.findall(r"```sql\s*(.*?)```", texto or "", flags=re.IGNORECASE | re.DOTALL)


def _tabla_markdown(columnas: list, filas: list) -> str:
    columnas = [str(columna) for columna in columnas] or ["resultado"]
    if not filas:
        return "_Sin filas._"

    def celda(valor) -> str:
        return str(valor).replace("|", "\\|").replace("\n", " ")

    lineas = [
        "| " + " | ".join(columnas) + " |",
        "| " + " | ".join("---" for _ in columnas) + " |",
    ]
    lineas.extend(
        "| " + " | ".join(celda(valor) for valor in fila) + " |"
        for fila in filas
    )
    return "\n".join(lineas)


def _numeros_de_texto(texto: str) -> list[float]:
    """Números de la narrativa, sin contar los bloques SQL."""
    sin_sql = re.sub(r"```.*?```", " ", texto or "", flags=re.DOTALL)
    patron = re.compile(r"(?<![\w.-])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\w.-])")
    numeros = []
    for coincidencia in patron.finditer(sin_sql):
        try:
            numeros.append(float(coincidencia.group(1).replace(",", "")))
        except ValueError:
            continue
    return numeros


def _valores_numericos(ejecutadas: list[dict]) -> list[float]:
    valores: list[float] = []
    for consulta in ejecutadas:
        for fila in consulta.get("filas") or []:
            for valor in fila:
                if isinstance(valor, bool):
                    continue
                if isinstance(valor, (int, float)):
                    valores.append(float(valor))
    return valores


def _cifras_coinciden(respuesta: str, ejecutadas: list[dict]) -> bool:
    """Comprueba que los números de la narrativa existan en los resultados.

    Es una verificación deliberadamente conservadora: ignora números pequeños
    (1-10), que suelen ser partes de frases, y acepta enteros redondeados y la
    suma de los grupos (por ejemplo, un total calculado a partir de una
    distribución).
    """
    valores = _valores_numericos(ejecutadas)
    if not valores:
        return True
    derivados = [sum(valores)]
    for numero in _numeros_de_texto(respuesta):
        if abs(numero) <= 10:
            continue
        if any(
            abs(numero - valor) < 0.01
            or abs(numero - round(valor)) <= 0.5
            or abs(numero - int(valor)) < 0.01
            for valor in [*valores, *derivados]
        ):
            continue
        return False
    return True


def _formatear_resultados(ejecutadas: list[dict]) -> str:
    partes = ["Resultado verificado de la base:"]
    for numero, consulta in enumerate(ejecutadas, start=1):
        sql = consulta.get("sql") or "(SQL no registrada)"
        partes.append(
            f"\n**Consulta {numero}**\n"
            f"```sql\n{sql}\n```\n"
            f"{_tabla_markdown(consulta.get('columnas') or [], consulta.get('filas') or [])}"
        )
        if consulta.get("truncado"):
            partes.append("\n_Resultado truncado a 50 filas._")
    return "\n".join(partes)


class DataAnalystAgent:
    def __init__(self, url_db: str | None = None):
        self.nombre = "data_analyst"
        self.descripcion = (
            "Análisis cuantitativo con consultas SQL de solo lectura sobre la "
            "base de datos bancaria: conteos, sumas, promedios, rankings y "
            "comparaciones entre tablas."
        )
        self.modelo = "qwen2.5:7b-instruct"
        self.url_db = url_db or get_database_url()

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

    def _get_engine(self):
        if self._engine is None:
            if not self.url_db:
                raise RuntimeError(
                    "No hay base configurada: define DATABASE_URL o usa conectar()."
                )
            self._engine = create_readonly_engine(self.url_db)
        return self._engine

    def _objetos_disponibles(self, inspector) -> tuple[list[str], list[str]]:
        """Return the tables and views the analyst may use."""
        tablas = [t for t in inspector.get_table_names() if t not in TABLAS_EXCLUIDAS]
        vistas = [v for v in inspector.get_view_names() if v not in TABLAS_EXCLUIDAS]
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
            problema = _validar_consulta(sql)
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
            if sensibles:
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

    def _get_agente(self):
        if self._agente is None:
            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agente = create_agent(
                model=llm,
                tools=self._tools,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agente

    def procesar(self, consulta: str, contexto: str = "") -> str:
        if contexto.strip():
            consulta = (
                "Contexto de la conversación anterior. Úsalo para interpretar "
                "la consulta actual, pero no respondas a ese contexto por "
                "separado:\n"
                f"{contexto.strip()}\n\n"
                "Consulta actual:\n"
                f"{consulta}"
            )
        result = self._get_agente().invoke(
            {"messages": [{"role": "user", "content": consulta}]},
            config={"recursion_limit": 25},
        )
        mensajes = result["messages"]
        respuesta = str(mensajes[-1].content)
        ejecutadas = _consultas_ejecutadas(mensajes)

        # El modelo pequeño a veces describe las consultas y luego inventa
        # cifras sin llamar a la herramienta. En ese caso preferimos no dar
        # números a devolver una respuesta con datos no verificados.
        if not ejecutadas:
            if (
                not _SIN_DATOS.search(respuesta)
                and (_CIFRAS.search(respuesta) or _PIDE_HERRAMIENTA.search(respuesta))
            ):
                return _MENSAJE_SIN_SQL
            return respuesta

        # El modelo también puede ejecutar una consulta y luego escribir otra
        # distinta en la respuesta final. Si no coincide, mostramos el
        # resultado real que devolvió la base, no la narración del modelo.
        finales = _bloques_sql(respuesta)
        if not finales:
            return _formatear_resultados(ejecutadas)

        ejecutadas_norm = {
            _normalizar_sql(consulta["sql"])
            for consulta in ejecutadas
            if consulta.get("sql")
        }
        finales_norm = {_normalizar_sql(sql) for sql in finales}
        if not finales_norm.issubset(ejecutadas_norm):
            return (
                "La consulta que mostró el modelo no coincide con la que se "
                "ejecutó. Estos son los datos reales:\n\n"
                + _formatear_resultados(ejecutadas)
            )
        if _valores_numericos(ejecutadas) and not _numeros_de_texto(respuesta):
            return (
                "La respuesta no incluyó los valores devueltos por la "
                "consulta. Muestro el resultado real:\n\n"
                + _formatear_resultados(ejecutadas)
            )
        if not _cifras_coinciden(respuesta, ejecutadas):
            return (
                "Los números del texto no coinciden con los valores que "
                "devolvió la consulta. Muestro el resultado real:\n\n"
                + _formatear_resultados(ejecutadas)
            )
        return respuesta

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_analyst_agent = DataAnalystAgent()
