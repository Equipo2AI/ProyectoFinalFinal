"""Servidor STDIO independiente de las herramientas SQL de Data Analyst."""

import os

from fastmcp import FastMCP

from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend

mcp = FastMCP("Data Analyst SQL MCP")
# Solo el backend local: no se instancia el agente cliente ni se llama procesar().
_backend = DataAnalystSQLBackend()
# El perfil viaja en el entorno del subproceso, no en los argumentos que ve el
# modelo. Así las tools de bajo nivel también aplican row-level security.
_backend.configurar_perfil({
    "rol": os.environ.get("DATA_ANALYST_ROL", ""),
    "cliente_id": os.environ.get("DATA_ANALYST_CLIENTE_ID"),
})
_herramientas = {herramienta.name: herramienta for herramienta in _backend._tools}


@mcp.tool()
def analizar_datos(
    consulta: str, contexto: str = "", perfil: dict | None = None
) -> str:
    """Responde una pregunta cuantitativa en lenguaje natural usando el backend SQL local.

    `contexto` es opcional y conserva el hilo de una pregunta anterior. `perfil`
    describe la sesión autenticada (rol y cliente_id) para limitar o ampliar el
    alcance de los datos. El agente usa las herramientas SQL del backend, sin
    volver a llamar por MCP.
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
def validar_sql(sql: str) -> str:
    """Valida una consulta SELECT candidata sin ejecutarla."""
    return _herramientas["validar_sql"].invoke({"sql": sql})


@mcp.tool()
def ejecutar_consulta(sql: str) -> str:
    """Ejecuta una consulta SQL de solo lectura validada y devuelve hasta 50 filas."""
    return _herramientas["ejecutar_consulta"].invoke({"sql": sql})


@mcp.tool()
def graficar_consulta(
    sql: str,
    tipo: str = "auto",
    eje_x: str = "",
    eje_y: str = "",
    titulo: str = "",
    orden: str = "ninguno",
    perfil: dict | None = None,
) -> str:
    """Valida y ejecuta una consulta agregada, y devuelve una gráfica PNG en base64.

    El perfil solo se aplica cuando el orquestador lo envía; en el flujo interno
    del agente se conserva el perfil configurado al iniciar el subproceso.
    """
    if perfil is not None:
        _backend.configurar_perfil(perfil)
    return _herramientas["graficar_consulta"].invoke({
        "sql": sql,
        "tipo": tipo,
        "eje_x": eje_x,
        "eje_y": eje_y,
        "titulo": titulo,
        "orden": orden,
    })


@mcp.tool()
def catalogo_metricas() -> str:
    """Lista las métricas certificadas y sus dimensiones permitidas."""
    return _herramientas["catalogo_metricas"].invoke({})


@mcp.tool()
def consultar_metrica(
    metrica: str,
    agrupar_por: str = "",
    filtros: dict | None = None,
    limite: int = 20,
    orden: str = "desc",
    perfil: dict | None = None,
) -> str:
    """Consulta una métrica certificada sin aceptar SQL libre."""
    if perfil is not None:
        _backend.configurar_perfil(perfil)
    return _herramientas["consultar_metrica"].invoke({
        "metrica": metrica,
        "agrupar_por": agrupar_por,
        "filtros": filtros or {},
        "limite": limite,
        "orden": orden,
    })


@mcp.tool()
def resolver_periodo(expresion: str, hoy: str = "") -> str:
    """Convierte una expresión de periodo a fechas y claves de dim_fecha."""
    return _herramientas["resolver_periodo"].invoke({
        "expresion": expresion,
        "hoy": hoy,
    })


@mcp.tool()
def resolver_valor(
    tabla: str, columna: str, valor: str, perfil: dict | None = None
) -> str:
    """Encuentra el valor canónico de una dimensión dentro del alcance de la sesión."""
    if perfil is not None:
        _backend.configurar_perfil(perfil)
    return _herramientas["resolver_valor"].invoke({
        "tabla": tabla,
        "columna": columna,
        "valor": valor,
    })


@mcp.tool()
def verificar_cifras(texto: str, evidencia: str = "") -> str:
    """Contrasta cifras y PII de un texto contra la evidencia o la última consulta."""
    return _herramientas["verificar_cifras"].invoke({
        "texto": texto,
        "evidencia": evidencia,
    })


if __name__ == "__main__":
    mcp.run()
