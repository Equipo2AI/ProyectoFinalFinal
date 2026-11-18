"""Data Analyst SQL: agente LangChain y cliente del servidor MCP independiente."""

from langchain_core.tools import tool

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend
from proyecto.mcp_servers.mcp_client import llamar_herramienta, llamar_herramienta_mcp


class DataAnalystAgent(DataAnalystSQLBackend):
    """Conserva url_db/conectar y usa MCP para las herramientas SQL."""

    def procesar(self, consulta: str, contexto: str = "", perfil: dict | None = None) -> str:
        """STDIO tambien para procesar; el servidor aloja solo el backend local."""
        return self._llamar_mcp("analizar_datos", {
            "consulta": consulta, "contexto": contexto, "perfil": self._perfil_actual(),
        })

    def _entorno_mcp(self) -> dict[str, str]:
        # La URL y el perfil viajan solo en el entorno del subproceso, nunca
        # como argumento visible al modelo. El valor vacio impide tomar otra
        # BD del .env hijo.
        entorno = {"DATABASE_URL": self.url_db or ""}
        if self._rol_actual:
            entorno["DATA_ANALYST_ROL"] = self._rol_actual
        if self._cliente_actual is not None:
            entorno["DATA_ANALYST_CLIENTE_ID"] = str(self._cliente_actual)
        return entorno

    def _llamar_mcp(self, nombre: str, argumentos: dict) -> str:
        return llamar_herramienta(
            "proyecto.mcp_servers.data_analyst_server", nombre, argumentos,
            env=self._entorno_mcp(),
        )

    async def _llamar_mcp_async(self, nombre: str, argumentos: dict) -> str:
        return await llamar_herramienta_mcp(
            "proyecto.mcp_servers.data_analyst_server", nombre, argumentos,
            env=self._entorno_mcp(),
        )

    def _crear_herramientas(self):
        @tool
        def listar_tablas() -> str:
            """Lista las tablas y vistas de la base de datos y el motor SQL (sqlite, postgresql...)."""
            return self._llamar_mcp("listar_tablas", {})

        @tool
        def describir_tabla(tabla: str) -> str:
            """Devuelve columnas, tipos, numero de filas y relaciones. Usala antes de escribir SQL."""
            return self._llamar_mcp("describir_tabla", {"tabla": tabla})

        @tool
        def valores_columna(tabla: str, columna: str) -> str:
            """Devuelve los valores distintos mas frecuentes de una columna categorica. Usala antes de WHERE."""
            return self._llamar_mcp("valores_columna", {"tabla": tabla, "columna": columna})

        @tool
        def validar_sql(sql: str) -> str:
            """Valida una consulta SELECT candidata antes de ejecutarla."""
            return self._llamar_mcp("validar_sql", {"sql": sql})

        @tool
        def ejecutar_consulta(sql: str) -> str:
            """Ejecuta UNA consulta SELECT de solo lectura y devuelve hasta 50 filas en JSON."""
            return self._llamar_mcp("ejecutar_consulta", {"sql": sql})

        @tool
        def graficar_consulta(
            sql: str,
            tipo: str = "auto",
            eje_x: str = "",
            eje_y: str = "",
            titulo: str = "",
            orden: str = "ninguno",
        ) -> str:
            """Genera una gráfica PNG a partir de una consulta agregada de solo lectura.

            Devuelve `png_base64`, la especificación usada y las filas. La
            consulta pasa por las mismas validaciones de SQL y privacidad.
            """
            return self._llamar_mcp("graficar_consulta", {
                "sql": sql,
                "tipo": tipo,
                "eje_x": eje_x,
                "eje_y": eje_y,
                "titulo": titulo,
                "orden": orden,
            })

        @tool
        def catalogo_metricas() -> str:
            """Lista métricas certificadas y sus dimensiones permitidas."""
            return self._llamar_mcp("catalogo_metricas", {})

        @tool
        def consultar_metrica(
            metrica: str,
            agrupar_por: str = "",
            filtros: dict | None = None,
            limite: int = 20,
            orden: str = "desc",
        ) -> str:
            """Consulta una métrica certificada sin escribir SQL libre."""
            return self._llamar_mcp("consultar_metrica", {
                "metrica": metrica,
                "agrupar_por": agrupar_por,
                "filtros": filtros or {},
                "limite": limite,
                "orden": orden,
            })

        @tool
        def resolver_periodo(expresion: str, hoy: str = "") -> str:
            """Convierte una expresión de periodo a fechas y claves de dim_fecha."""
            return self._llamar_mcp("resolver_periodo", {
                "expresion": expresion,
                "hoy": hoy,
            })

        @tool
        def resolver_valor(tabla: str, columna: str, valor: str) -> str:
            """Encuentra el valor canónico de una dimensión antes de filtrar."""
            return self._llamar_mcp("resolver_valor", {
                "tabla": tabla,
                "columna": columna,
                "valor": valor,
            })

        @tool
        def verificar_cifras(texto: str, evidencia: str = "") -> str:
            """Contrasta cifras y PII de un texto contra la evidencia o la última consulta."""
            return self._llamar_mcp("verificar_cifras", {
                "texto": texto,
                "evidencia": evidencia,
            })

        def crear_corutina(nombre):
            async def ejecutar(**argumentos):
                return await self._llamar_mcp_async(nombre, argumentos)
            return ejecutar

        herramientas = [
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
        for herramienta in herramientas:
            herramienta.coroutine = crear_corutina(herramienta.name)
        return herramientas

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_analyst_agent = DataAnalystAgent()
