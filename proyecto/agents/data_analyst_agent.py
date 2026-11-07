"""Data Analyst SQL: agente LangChain y cliente del servidor MCP independiente."""

from langchain_core.tools import tool

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend
from proyecto.mcp_servers.mcp_client import llamar_herramienta, llamar_herramienta_mcp


class DataAnalystAgent(DataAnalystSQLBackend):
    """Conserva url_db/conectar y usa MCP para las cuatro herramientas SQL."""

    def _entorno_mcp(self) -> dict[str, str]:
        # La URL viaja solo en el entorno del subproceso, nunca como argumento
        # visible al modelo. El valor vacio impide tomar otra BD del .env hijo.
        return {"DATABASE_URL": self.url_db or ""}

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
        def ejecutar_consulta(sql: str) -> str:
            """Ejecuta UNA consulta SELECT de solo lectura y devuelve hasta 50 filas en JSON."""
            return self._llamar_mcp("ejecutar_consulta", {"sql": sql})

        def crear_corutina(nombre):
            async def ejecutar(**argumentos):
                return await self._llamar_mcp_async(nombre, argumentos)
            return ejecutar

        herramientas = [listar_tablas, describir_tabla, valores_columna, ejecutar_consulta]
        for herramienta in herramientas:
            herramienta.coroutine = crear_corutina(herramienta.name)
        return herramientas

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_analyst_agent = DataAnalystAgent()
