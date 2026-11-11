"""Backend SQL de Data Analyst; conserva las herramientas y controles del equipo."""

import base64
import difflib
import json
import os
import unicodedata

from dotenv import load_dotenv
from langchain_core.tools import tool
from sqlalchemy import create_engine, event, inspect

from proyecto.charts import generar_grafica
from proyecto.config.models import modelo_de
from proyecto.guardrails import redactar_pii, verificar_cifras as verificar_cifras_texto
from proyecto.mcp_servers.sql_guard import (
    ResultadoValidacionSQL,
    validar_sql as validar_sql_guard,
)
from proyecto.metrics import Metrica
from proyecto.metrics import catalogo_metricas as listar_catalogo_metricas
from proyecto.metrics import obtener_metrica
from proyecto.periods import resolver_periodo as resolver_periodo_determinista
from proyecto.security.local_auth import audit, principal

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
5. Valida el SQL con validar_sql antes de ejecutarlo. La validación revisa
   catálogo, solo lectura, PII y el filtro del cliente autenticado.
6. Ejecuta una sola consulta SELECT con ejecutar_consulta. Prefiere
   agregaciones (COUNT, SUM, AVG, GROUP BY) y usa LIMIT al listar filas.
7. Si la pregunta coincide con una métrica certificada, usa catalogo_metricas
   y consultar_metrica antes de escribir SQL libre. Usa resolver_periodo para
   fechas y resolver_valor para categorías ambiguas.
8. No generes imágenes ni código de gráficas: entrega el SQL y los datos; el
   orquestador se encarga de dibujar si el usuario lo pidió.
9. Antes de cerrar, usa verificar_cifras si quieres contrastar tu redacción
   contra los datos. No publiques cifras que no aparezcan en la evidencia.
10. Si la herramienta devuelve un error, corrige la consulta y reintenta
    (máximo 3 intentos).
11. Responde en español: la conclusión, los números que la respaldan y la
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
        self._ultimo_resultado: dict | None = None
        self._ultimo_sql: str | None = None
        self._tools = self._crear_herramientas()
        self.herramientas = [t.name for t in self._tools]

    def conectar(self, url_db: str) -> None:
        """Point the agent to another database without recreating it."""
        if self._engine is not None:
            self._engine.dispose()
        self.url_db = url_db
        self._engine = None
        self._agentes = {}

    def configurar_perfil(self, perfil: dict | None) -> None:
        """Fija el perfil que usan las tools de esta instancia.

        El perfil no es un argumento de las tools SQL: debe venir de la sesión
        autenticada, no del texto que genera el modelo.
        """
        self._rol_actual, self._cliente_actual = _normalizar_perfil(perfil)

    def _perfil_actual(self) -> dict[str, object]:
        """Combina el perfil explícito con la identidad local autenticada.

        La UI usa ``authenticated`` y no pasa ``perfil``. Las pruebas y el CLI
        pueden fijar el perfil explícitamente; en ese caso tiene prioridad.
        """
        rol = self._rol_actual
        cliente_id = self._cliente_actual
        if rol == "usuario" and cliente_id is None:
            try:
                user = principal()
            except Exception:  # noqa: BLE001 - sin sesión se conserva el modo demo
                user = None
            if user:
                if user.get("role") == "auditor":
                    rol = "superusuario"
                elif user.get("role") == "normal":
                    clientes = list(user.get("customers") or [])
                    if len(clientes) == 1:
                        cliente_id = int(clientes[0])
        return {"rol": rol, "cliente_id": cliente_id}

    @staticmethod
    def _auditar(action: str, allowed: bool, sql: str | None = None) -> None:
        """Registra el evento sin guardar SQL literal ni resultados."""
        try:
            user = principal()
            audit(action, allowed, user.get("subject"), sql)
        except Exception:  # noqa: BLE001 - la auditoría no debe romper la tool
            pass

    def validar_sql(
        self, sql: str, perfil: dict | None = None
    ) -> ResultadoValidacionSQL:
        """Valida una consulta candidata contra catálogo y perfil actuales."""
        return validar_sql_guard(
            sql,
            engine=self._get_engine(),
            perfil=perfil or self._perfil_actual(),
            tablas_excluidas=TABLAS_EXCLUIDAS,
            columnas_sensibles=COLUMNAS_SENSIBLES,
            limite_max=MAX_FILAS + 1,
        )

    def _consultar(self, sql: str, *, confiar_en_alcance: bool = False) -> dict:
        """Valida y ejecuta una consulta; devuelve un dict serializable.

        ``confiar_en_alcance`` se usa solo con los planes deterministas de
        ``banking_queries``: su SQL lo escribe el servidor y ya se ejecuta
        contra la proyección SQLite acotada a la identidad autenticada.
        """
        perfil_validacion = (
            {"rol": "superusuario"} if confiar_en_alcance else self._perfil_actual()
        )
        try:
            validacion = self.validar_sql(sql, perfil=perfil_validacion)
        except Exception as error:  # noqa: BLE001 - se devuelve al hub
            self._auditar("sql_validation", False, sql)
            return {"error": f"{type(error).__name__}: {error}"}
        if not validacion.ok:
            self._auditar("sql_validation", False, sql)
            return {
                "error": " ".join(validacion.errores),
                "errores": validacion.errores,
                "validacion": validacion.model_dump(),
            }

        try:
            with self._get_engine().connect() as conexion:
                resultado = conexion.exec_driver_sql(
                    validacion.normalizado or sql.strip().rstrip(";")
                )
                columnas = list(resultado.keys())
                filas = resultado.fetchmany(MAX_FILAS + 1)
        except Exception as error:
            # El mensaje del motor le sirve al LLM para corregir su SQL.
            self._auditar("sql_execute", False, sql)
            return {"error": f"{type(error).__name__}: {str(error).splitlines()[0]}"}

        sensibles = [
            columna
            for columna in columnas
            if _normalizar(columna) in COLUMNAS_SENSIBLES
        ]
        if (
            sensibles
            and not confiar_en_alcance
            and self._perfil_actual()["rol"] != "superusuario"
        ):
            self._auditar("sql_pii", False, sql)
            return {
                "error": (
                    f"La consulta devuelve datos personales "
                    f"({', '.join(sensibles)}). Usa agregados (COUNT, AVG...) "
                    "en su lugar."
                )
            }

        resultado = {
            "columnas": columnas,
            "filas": [list(fila) for fila in filas[:MAX_FILAS]],
            "truncado": len(filas) > MAX_FILAS,
        }
        self._ultimo_resultado = resultado
        self._ultimo_sql = validacion.normalizado or sql.strip().rstrip(";")
        self._auditar("sql_execute", True, sql)
        return resultado

    def _get_agente(self, rol: str = "", cliente_id: int | None = None):
        clave = f"{rol}:{cliente_id}" if rol else "normal"
        if clave not in self._agentes:
            from langchain.agents import create_agent
            from langchain_ollama import ChatOllama

            llm = ChatOllama(
                model=self.modelo,
                temperature=0,
                reasoning=False,
                timeout=45,
            )
            # La gráfica la genera el orquestador o el hub con una tool
            # determinista. El agente interno no debe recibir el PNG en base64
            # ni incrustarlo en su respuesta.
            herramientas = [
                herramienta
                for herramienta in self._tools
                if herramienta.name != "graficar_consulta"
            ]
            prompt = _prompt_sesion(rol, cliente_id) + (
                "\nLa base ya está limitada al alcance autenticado; 'mi' se "
                "refiere a ese alcance, no a un ID escrito en el chat. No pidas "
                "más identidad. Solo un auditor con permiso explícito de un campo "
                "personal puede usarlo si la herramienta se lo entrega para su "
                "auditoría."
            )
            self._agentes[clave] = create_agent(
                model=llm,
                tools=herramientas,
                system_prompt=prompt,
            )
        return self._agentes[clave]

    def procesar(
        self,
        consulta: str,
        contexto: str = "",
        perfil: dict | None = None,
    ) -> str:
        from proyecto.security.local_auth import authorize_request

        try:
            user = authorize_request(consulta)
        except PermissionError as error:
            return str(error)

        if user["role"] == "auditor" and "audit" not in user["permissions"]:
            principal("aggregate")
        audit("analizar_datos", True, user["subject"])

        if perfil:
            self.configurar_perfil(perfil)
        elif user["role"] == "auditor":
            self.configurar_perfil({"rol": "superusuario"})
        else:
            clientes = list(user.get("customers") or [])
            self.configurar_perfil(
                {
                    "rol": "usuario",
                    "cliente_id": clientes[0] if len(clientes) == 1 else None,
                }
            )

        rol, cliente_id = self._rol_actual, self._cliente_actual
        sesion = _contexto_sesion(rol, cliente_id)
        if sesion:
            contexto = f"{contexto}\n\n{sesion}".strip()

        from proyecto.agents.banking_queries import (
            format_result,
            normalize,
            plan_query,
        )

        plan = plan_query(consulta)
        if plan:
            title, sql = plan
            result = self._consultar(sql, confiar_en_alcance=True)
            answer = format_result(title, result, sql)
            if "estado de cuenta" in normalize(consulta):
                balance = plan_query(
                    normalize(consulta).replace("estado de cuenta", "saldo")
                )
                if balance:
                    title2, sql2 = balance
                    result2 = self._consultar(sql2, confiar_en_alcance=True)
                    answer = (
                        format_result(title2, result2, sql2)
                        + "\n\n"
                        + answer
                    )
            return answer

        contenido = consulta
        if contexto.strip():
            contenido = (
                f"Contexto de la conversación:\n{contexto}\n\nPregunta: {consulta}"
            )
        from langsmith import tracing_context

        with tracing_context(enabled=False):
            resultado = self._get_agente(rol, cliente_id).invoke(
                {"messages": [{"role": "user", "content": contenido}]},
                config={"recursion_limit": 25},
            )
        return resultado["messages"][-1].content

    def _get_engine(self):
        try:
            user = principal()
        except Exception:  # noqa: BLE001 - demo, CLI y pruebas sin sesión
            user = None

        if user is None:
            if self._engine is not None and getattr(self, "_identity", None) is not None:
                self._engine.dispose()
                self._engine = None
                self._agentes = {}
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
                self._identity = None
            return self._engine

        identity = (
            user["subject"],
            tuple(user["customers"]),
            tuple(user["permissions"]),
        )
        if self._engine is not None and getattr(self, "_identity", None) != identity:
            self._engine.dispose()
            self._engine = None
            self._agentes = {}
        if self._engine is None:
            from sqlalchemy.engine import make_url
            from proyecto.security.scoped_sqlite import connect_scoped

            if not self.url_db:
                raise PermissionError("No hay base autorizada.")
            url = make_url(self.url_db)
            if url.drivername != "sqlite":
                raise PermissionError(
                    "El control local de alcance solo admite SQLite."
                )
            self._engine = create_engine(
                "sqlite://", creator=lambda: connect_scoped(url.database)
            )
            self._identity = identity
        return self._engine

    def _excluidas(self) -> set[str]:
        try:
            user = principal()
        except Exception:  # noqa: BLE001 - pruebas y CLI sin sesión
            user = None
        if user:
            pii = "audit" in user["permissions"] and any(
                p.startswith("pii:") for p in user["permissions"]
            )
        else:
            pii = self._perfil_actual()["rol"] == "superusuario"
        return TABLAS_EXCLUIDAS - ({"dim_cliente_persona"} if pii else set())

    def _objetos_disponibles(
        self, inspector, permitir_pii: bool | None = None
    ) -> tuple[list[str], list[str]]:
        """Return the tables and views the analyst may use."""
        if permitir_pii is None:
            excluidas = self._excluidas()
        else:
            excluidas = TABLAS_EXCLUIDAS - (
                {"dim_cliente_persona"} if permitir_pii else set()
            )
        tablas = [t for t in inspector.get_table_names() if t not in excluidas]
        vistas = [v for v in inspector.get_view_names() if v not in excluidas]
        return tablas, vistas

    def _filtro_sesion(
        self, tabla: str, columnas: list[str]
    ) -> tuple[str, dict[str, int]]:
        """Devuelve un WHERE de alcance para las tools auxiliares.

        Las herramientas que no reciben SQL (``describir_tabla`` y
        ``valores_columna``) también deben respetar la sesión: de lo contrario
        un usuario podría listar los ``num_cte`` de todos los clientes.
        """
        if self._rol_actual != "usuario" or self._cliente_actual is None:
            return "", {}
        if "num_cte" in columnas:
            return " WHERE num_cte = :cliente_id", {
                "cliente_id": self._cliente_actual
            }
        if "num_cta" in columnas:
            return (
                " WHERE num_cta IN ("
                "SELECT num_cta FROM dim_cuenta WHERE num_cte = :cliente_id"
                ")",
                {"cliente_id": self._cliente_actual},
            )
        return "", {}

    @staticmethod
    def _literal_sql(valor) -> str:
        if isinstance(valor, bool) or valor is None:
            raise ValueError("El filtro debe ser un texto o un número.")
        if isinstance(valor, (int, float)):
            return str(valor)
        texto = str(valor)
        if len(texto) > 120:
            raise ValueError("El valor del filtro es demasiado largo.")
        return "'" + texto.replace("'", "''") + "'"

    def _sql_metrica(
        self,
        metrica: Metrica,
        agrupar_por: str = "",
        filtros: dict | None = None,
        limite: int = 20,
        orden: str = "desc",
    ) -> str:
        """Construye SQL desde el catálogo; nunca acepta tabla/columna libres."""
        motor = self._get_engine()
        inspector = inspect(motor)
        try:
            columnas = [
                columna["name"]
                for columna in inspector.get_columns(metrica.vista)
            ]
        except Exception as error:  # noqa: BLE001 - catálogo desalineado
            raise ValueError(
                f"La vista de la métrica no existe: {metrica.vista}"
            ) from error

        requeridas = {metrica.columna, *metrica.dimensiones}
        faltantes = sorted(requeridas - set(columnas))
        if faltantes:
            raise ValueError(
                f"La métrica {metrica.nombre} no coincide con la vista: "
                f"faltan {', '.join(faltantes)}."
            )

        agrupar_por = str(agrupar_por or "").strip()
        if agrupar_por and agrupar_por not in metrica.dimensiones:
            raise ValueError(
                f"No se puede agrupar {metrica.nombre} por '{agrupar_por}'. "
                f"Dimensiones: {', '.join(metrica.dimensiones)}."
            )

        filtros = dict(filtros or {})
        condiciones: list[str] = []
        filtro_sesion, _ = self._filtro_sesion(metrica.vista, columnas)
        if filtro_sesion:
            condiciones.append(
                filtro_sesion.strip()[len("WHERE"):].strip().replace(
                    ":cliente_id", str(int(self._cliente_actual))
                )
            )
        for columna, valor in filtros.items():
            if columna not in metrica.columnas_filtrables():
                raise ValueError(
                    f"El filtro '{columna}' no aplica a {metrica.nombre}."
                )
            condiciones.append(
                f"{motor.dialect.identifier_preparer.quote(columna)} = "
                f"{self._literal_sql(valor)}"
            )

        q = motor.dialect.identifier_preparer.quote
        if metrica.agregacion == "count":
            expresion = f"COUNT({q(metrica.columna)})"
        elif metrica.agregacion == "count_distinct":
            expresion = f"COUNT(DISTINCT {q(metrica.columna)})"
        else:
            expresion = f"{metrica.agregacion.upper()}({q(metrica.columna)})"

        if agrupar_por:
            seleccion = f"{q(agrupar_por)} AS {q(agrupar_por)}, "
            grupo = f" GROUP BY {q(agrupar_por)}"
        else:
            seleccion = ""
            grupo = ""

        if orden not in {"asc", "desc", "ninguno"}:
            raise ValueError("El orden debe ser 'asc', 'desc' o 'ninguno'.")
        orden_sql = (
            f" ORDER BY {q(metrica.nombre)} {orden.upper()}"
            if orden != "ninguno"
            else ""
        )
        limite = max(1, min(int(limite), MAX_FILAS))
        where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        return (
            f"SELECT {seleccion}{expresion} AS {q(metrica.nombre)} "
            f"FROM {q(metrica.vista)}{where}{grupo}{orden_sql} LIMIT {limite}"
        )

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

                columnas = inspector.get_columns(tabla)
                nombre_seguro = engine.dialect.identifier_preparer.quote(tabla)
                filtro, parametros = self._filtro_sesion(
                    tabla, [c["name"] for c in columnas]
                )
                with engine.connect() as conexion:
                    filas = conexion.exec_driver_sql(
                        f"SELECT COUNT(*) FROM {nombre_seguro}{filtro}",
                        parametros,
                    ).scalar()

                return _json({
                    "tabla": tabla,
                    "es_vista": tabla in vistas,
                    "filas": filas,
                    "columnas": [
                        {"nombre": c["name"], "tipo": str(c["type"])}
                        for c in columnas
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
                and self._perfil_actual()["rol"] != "superusuario"
            ):
                return _json({"error": "Columna con datos personales: no se muestran valores."})
            try:
                engine = self._get_engine()
                inspector = inspect(engine)
                tablas, vistas = self._objetos_disponibles(inspector)
                if tabla not in tablas + vistas:
                    return _json({"error": f"La tabla '{tabla}' no existe o no está disponible."})
                columnas_tabla = [c["name"] for c in inspector.get_columns(tabla)]
                if columna not in columnas_tabla:
                    return _json({"error": f"La columna '{columna}' no existe en '{tabla}'."})

                q = engine.dialect.identifier_preparer.quote
                filtro, parametros = self._filtro_sesion(tabla, columnas_tabla)
                sql = (
                    f"SELECT {q(columna)} AS valor, COUNT(*) AS veces FROM {q(tabla)}"
                    f"{filtro} GROUP BY {q(columna)} ORDER BY veces DESC LIMIT 20"
                )
                with engine.connect() as conexion:
                    filas = conexion.exec_driver_sql(sql, parametros).fetchall()
                return _json({
                    "tabla": tabla,
                    "columna": columna,
                    "valores": [list(fila) for fila in filas],
                })
            except Exception as error:
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def validar_sql(sql: str) -> str:
            """Valida una consulta SELECT candidata: una sola sentencia, tablas/columnas existentes, sin PII no autorizada y con filtro de cliente cuando aplica. Úsala antes de ejecutar_consulta."""
            try:
                return _json(self.validar_sql(sql).model_dump())
            except Exception as error:
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def ejecutar_consulta(sql: str) -> str:
            """Ejecuta UNA consulta SELECT de solo lectura y devuelve hasta 50 filas en JSON."""
            return _json(self._consultar(sql))

        @tool
        def graficar_consulta(
            sql: str,
            tipo: str = "auto",
            eje_x: str = "",
            eje_y: str = "",
            titulo: str = "",
            orden: str = "ninguno",
        ) -> str:
            """Valida y ejecuta una consulta agregada, y devuelve una gráfica PNG en base64.

            Úsala solo si el usuario pide una gráfica o visualización. La consulta
            debe devolver pocas filas agregadas (máximo 50), con una columna
            categórica y otra numérica. No grafiques identificadores ni datos
            personales.
            """
            datos = self._consultar(sql)
            if "error" in datos:
                return _json(datos)
            if datos.get("truncado"):
                return _json({
                    "error": (
                        "El resultado está truncado; agrega una agrupación o un "
                        "LIMIT antes de graficar."
                    )
                })

            try:
                grafica = generar_grafica(
                    datos["columnas"],
                    datos["filas"],
                    tipo=tipo,
                    eje_x=eje_x,
                    eje_y=eje_y,
                    titulo=titulo,
                    orden=orden,
                )
            except Exception as error:  # noqa: BLE001 - se devuelve al hub
                return _json({"error": f"{type(error).__name__}: {error}"})

            return _json({
                "png_base64": base64.b64encode(grafica["png"]).decode("ascii"),
                "especificacion": grafica["especificacion"],
                "advertencias": grafica["advertencias"],
                "columnas": datos["columnas"],
                "filas": datos["filas"],
            })

        @tool
        def catalogo_metricas() -> str:
            """Lista métricas certificadas, su unidad y las dimensiones permitidas."""
            return _json({"metricas": listar_catalogo_metricas()})

        @tool
        def consultar_metrica(
            metrica: str,
            agrupar_por: str = "",
            filtros: dict | None = None,
            limite: int = 20,
            orden: str = "desc",
        ) -> str:
            """Ejecuta una métrica certificada del catálogo, sin escribir SQL libre.

            `agrupar_por` debe ser una dimensión de la métrica. `filtros` solo
            acepta columnas declaradas. Ideal para conteos, saldos e importes
            cuando no necesitas una consulta ad hoc.
            """
            definicion = obtener_metrica(metrica)
            if definicion is None:
                return _json({
                    "error": f"La métrica '{metrica}' no existe en el catálogo.",
                    "metricas": [m["nombre"] for m in listar_catalogo_metricas()],
                })
            try:
                sql = self._sql_metrica(
                    definicion,
                    agrupar_por=agrupar_por,
                    filtros=filtros or {},
                    limite=limite,
                    orden=str(orden or "desc").strip().lower(),
                )
            except Exception as error:  # noqa: BLE001 - se devuelve al hub
                return _json({"error": f"{type(error).__name__}: {error}"})

            datos = self._consultar(sql)
            if "error" not in datos:
                datos.update({
                    "metrica": definicion.nombre,
                    "descripcion": definicion.descripcion,
                    "unidad": definicion.unidad,
                    "agrupacion": agrupar_por,
                    "sql": sql,
                })
            return _json(datos)

        @tool
        def resolver_periodo(expresion: str, hoy: str = "") -> str:
            """Convierte una expresión de periodo a fechas y claves de dim_fecha.

            Acepta 2026, 2026-02, 2026-02-15, febrero 2026, 1T 2026, 2026-Q2,
            este mes, mes pasado, este año, último trimestre o últimos 30 días.
            No adivina expresiones fuera de ese formato.
            """
            try:
                periodo = resolver_periodo_determinista(
                    expresion, hoy=hoy or None
                )
                return _json(periodo.model_dump())
            except Exception as error:  # noqa: BLE001 - se devuelve al hub
                return _json({"error": f"{type(error).__name__}: {error}"})

        @tool
        def resolver_valor(tabla: str, columna: str, valor: str) -> str:
            """Encuentra el valor exacto de una dimensión antes de filtrar.

            Devuelve el valor canónico y las coincidencias más cercanas. No
            amplía el alcance de la sesión: usa los mismos controles que
            valores_columna.
            """
            datos = json.loads(
                valores_columna.invoke({"tabla": tabla, "columna": columna})
            )
            if "error" in datos:
                return _json(datos)
            objetivo = _normalizar(str(valor or ""))
            if not objetivo:
                return _json({"error": "El valor a resolver está vacío."})

            valores = [
                (str(fila[0]), int(fila[1]))
                for fila in datos.get("valores") or []
                if isinstance(fila, list) and len(fila) >= 2
            ]
            normalizados = {_normalizar(nombre): nombre for nombre, _ in valores}
            exacto = normalizados.get(objetivo)
            coincidencias = [
                {"valor": nombre, "veces": veces}
                for nombre, veces in valores
                if objetivo in _normalizar(nombre)
                or _normalizar(nombre) in objetivo
            ]
            if not coincidencias and not exacto:
                cercanos = difflib.get_close_matches(
                    objetivo, list(normalizados), n=5, cutoff=0.6
                )
                coincidencias = [
                    {"valor": normalizados[cercano], "veces": 0}
                    for cercano in cercanos
                ]

            elegido = exacto
            if elegido is None and len(coincidencias) == 1:
                elegido = coincidencias[0]["valor"]
            return _json({
                "tabla": tabla,
                "columna": columna,
                "solicitado": valor,
                "valor": elegido,
                "ambiguo": elegido is None and len(coincidencias) > 1,
                "coincidencias": coincidencias[:10],
            })

        @tool
        def verificar_cifras(texto: str, evidencia: str = "") -> str:
            """Verifica que las cifras del texto aparezcan en la evidencia.

            Si no se pasa evidencia, usa la última consulta ejecutada. También
            detecta correo, teléfono, CURP y RFC. Devuelve una versión
            redactada del texto; no modifica la base.
            """
            fuente = evidencia
            if not fuente:
                fuente = json.dumps(
                    {
                        "sql": self._ultimo_sql,
                        "resultado": self._ultimo_resultado,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            no_verificadas = verificar_cifras_texto(texto, fuente)
            redactado, pii = redactar_pii(texto)
            return _json({
                "ok": not no_verificadas and not pii,
                "cifras_no_verificadas": no_verificadas,
                "pii_detectada": pii,
                "texto_redactado": redactado,
            })

        return [
            listar_tablas,
            describir_tabla,
            valores_columna,
            validar_sql,
            ejecutar_consulta,
            graficar_consulta,
            catalogo_metricas,
            consultar_metrica,
            resolver_periodo,
            resolver_valor,
            verificar_cifras,
        ]
