"""Servidor STDIO independiente de las cuatro herramientas SQL de Data Analyst."""

from fastmcp import FastMCP

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend

mcp = FastMCP("Data Analyst SQL MCP")
# Solo el backend local: no se instancia el agente cliente ni se llama procesar().
_backend = DataAnalystSQLBackend()
_herramientas = {herramienta.name: herramienta for herramienta in _backend._tools}


@mcp.tool()
def analizar_datos(
    consulta: str, contexto: str = "", perfil: dict | None = None
) -> str:
    """Responde una pregunta cuantitativa en lenguaje natural usando el backend SQL local.

    `contexto` es opcional y conserva el hilo de una pregunta anterior. `perfil`
    describe la sesión autenticada (rol y cliente_id) para limitar o ampliar el
    alcance de los datos. El agente usa las cuatro herramientas SQL del backend,
    sin volver a llamar por MCP.
    """
    return _backend.procesar(consulta, contexto, perfil=perfil)


@mcp.tool()
def listar_tablas() -> str:
    """Lista tablas, vistas y motor SQL sin incluir las tablas excluidas."""
    return _herramientas["listar_tablas"].invoke({})


@mcp.tool()
def describir_tabla(tabla: str) -> str:
    """Devuelve columnas, tipos, conteo de filas y relaciones de una tabla permitida."""
    return _herramientas["describir_tabla"].invoke({"tabla": tabla})


@mcp.tool()
def valores_columna(tabla: str, columna: str) -> str:
    """Lista valores categoricos frecuentes aplicando los controles de privacidad."""
    return _herramientas["valores_columna"].invoke({"tabla": tabla, "columna": columna})


@mcp.tool()
def ejecutar_consulta(sql: str) -> str:
    """Ejecuta una consulta SQL de solo lectura validada y devuelve hasta 50 filas."""
    return _herramientas["ejecutar_consulta"].invoke({"sql": sql})


if __name__ == "__main__":
    mcp.run()
