"""Pruebas del hub de Data Analyst sin LLM ni MCP."""

from __future__ import annotations

import base64
import json

from proyecto.charts import generar_grafica
from proyecto.orchestration.hubs import HubDataAnalyst, HubLangChain
from proyecto.orchestration.tareas import (
    ContextoHub,
    DominioTarea,
    EstadoResultado,
    ResultadoTarea,
    Tarea,
)


def _tarea():
    return Tarea(
        id="sql-1",
        dominio=DominioTarea.DATA_ANALYST,
        instruccion="Cuenta los clientes por segmento.",
        objetivo="Conteo por segmento",
        resultado_esperado="Tabla con segmento y total",
    )


def _contexto():
    return ContextoHub(
        session_id="ses-1",
        perfil={"rol": "usuario", "cliente_id": 1},
    )


def _tarea_grafica():
    return Tarea(
        id="sql-2",
        dominio=DominioTarea.DATA_ANALYST,
        instruccion="Haz una gráfica de clientes por segmento.",
        objetivo="Gráfica por segmento",
        resultado_esperado="Gráfica de barras con el conteo por segmento",
    )


def test_hub_usa_tools_de_bajo_nivel_y_no_analizar_datos():
    hub = HubDataAnalyst()

    nombres = [tool.name for tool in hub.tools]

    assert nombres == [
        "listar_tablas",
        "describir_tabla",
        "valores_columna",
        "validar_sql",
        "ejecutar_consulta",
        "graficar_consulta",
        "catalogo_metricas",
        "consultar_metrica",
        "resolver_periodo",
        "resolver_valor",
        "verificar_cifras",
    ]
    assert "analizar_datos" not in nombres


def test_hub_configura_perfil_antes_de_ejecutar(monkeypatch):
    hub = HubDataAnalyst()
    capturado: dict = {}

    def ejecutar_falso(self, tarea, contexto):
        capturado["rol"] = self.backend._rol_actual
        capturado["cliente"] = self.backend._cliente_actual
        return ResultadoTarea(
            tarea_id=tarea.id,
            estado=EstadoResultado.OK,
            respuesta="ok",
        )

    monkeypatch.setattr(HubLangChain, "ejecutar", ejecutar_falso)

    hub.ejecutar(_tarea(), _contexto())

    assert capturado == {"rol": "usuario", "cliente": 1}


def test_contrasta_sql_y_datos_con_la_ejecucion_real():
    resultado = ResultadoTarea(
        tarea_id="sql-1",
        estado=EstadoResultado.OK,
        respuesta="El LLM dijo otra cosa.",
        sql="SELECT 'inventado'",
        datos={"inventado": True},
        trazas=[
            {
                "name": "ejecutar_consulta",
                "args": {"sql": "SELECT COUNT(*) AS n FROM dim_cliente"},
                "salida": json.dumps(
                    {"columnas": ["n"], "filas": [[10]], "truncado": False}
                ),
                "estado": "ok",
            }
        ],
    )

    refinado = HubDataAnalyst._contrastar_con_sql_ejecutado(resultado)

    assert refinado.sql == "SELECT COUNT(*) AS n FROM dim_cliente"
    assert refinado.datos == {
        "columnas": ["n"],
        "filas": [[10]],
        "truncado": False,
    }
    assert refinado.evidencia["filas"] == 1
    assert refinado.fuentes == ["banamex.db"]


def test_contrasta_metrica_certificada_con_su_sql():
    resultado = ResultadoTarea(
        tarea_id="sql-1",
        estado=EstadoResultado.OK,
        respuesta="El LLM dijo otra cosa.",
        trazas=[
            {
                "name": "consultar_metrica",
                "args": {"metrica": "clientes", "agrupar_por": "segmento"},
                "salida": json.dumps(
                    {
                        "columnas": ["segmento", "clientes"],
                        "filas": [["Banca masiva", 3750]],
                        "truncado": False,
                        "sql": "SELECT segmento, COUNT(DISTINCT num_cte) AS clientes FROM vw_cliente_publico GROUP BY segmento",
                    }
                ),
                "estado": "ok",
            }
        ],
    )

    refinado = HubDataAnalyst._contrastar_con_sql_ejecutado(resultado)

    assert refinado.sql.startswith("SELECT segmento")
    assert refinado.datos["filas"] == [["Banca masiva", 3750]]
    assert refinado.evidencia["filas"] == 1


def test_contrasta_error_de_ejecucion_como_error_de_tarea():
    resultado = ResultadoTarea(
        tarea_id="sql-1",
        estado=EstadoResultado.OK,
        respuesta="Respuesta del LLM",
        trazas=[
            {
                "name": "ejecutar_consulta",
                "args": {"sql": "SELECT columna_falsa FROM dim_cliente"},
                "salida": json.dumps({"error": "La columna no existe."}),
                "estado": "ok",
            }
        ],
    )

    refinado = HubDataAnalyst._contrastar_con_sql_ejecutado(resultado)

    assert refinado.estado is EstadoResultado.ERROR
    assert "no existe" in refinado.error


def test_hub_genera_grafica_automatica_si_el_usuario_la_pide():
    hub = HubDataAnalyst()
    resultado = ResultadoTarea(
        tarea_id="sql-2",
        estado=EstadoResultado.OK,
        respuesta="Hay 5,000 clientes.",
        sql="SELECT segmento, COUNT(*) AS clientes FROM vw_cliente_publico GROUP BY segmento",
        datos={
            "columnas": ["segmento", "clientes"],
            "filas": [
                ["Banca masiva", 3750],
                ["Banca preferente", 1000],
                ["Banca patrimonial", 250],
            ],
            "truncado": False,
        },
    )

    refinado = hub._agregar_grafica_si_aplica(resultado, _tarea_grafica())

    assert refinado.grafica is not None
    assert refinado.grafica.startswith(b"\x89PNG")
    assert refinado.grafica_especificacion["eje_x"] == "segmento"
    assert refinado.grafica_especificacion["eje_y"] == "clientes"
    assert "grafica" in refinado.evidencia


def test_hub_reutiliza_la_grafica_generada_por_la_tool():
    png = generar_grafica(
        ["segmento", "clientes"],
        [["Banca masiva", 10], ["Banca preferente", 5]],
        tipo="bar",
        eje_x="segmento",
        eje_y="clientes",
    )
    resultado = ResultadoTarea(
        tarea_id="sql-2",
        estado=EstadoResultado.OK,
        respuesta="Listo.",
        sql="SELECT segmento, COUNT(*) AS clientes FROM vw_cliente_publico GROUP BY segmento",
        datos={
            "columnas": ["segmento", "clientes"],
            "filas": [["Banca masiva", 10], ["Banca preferente", 5]],
            "truncado": False,
        },
        trazas=[
            {
                "name": "graficar_consulta",
                "args": {
                    "sql": "SELECT segmento, COUNT(*) AS clientes FROM vw_cliente_publico GROUP BY segmento"
                },
                "salida": json.dumps(
                    {
                        "png_base64": base64.b64encode(png["png"]).decode("ascii"),
                        "especificacion": png["especificacion"],
                    }
                ),
                "estado": "ok",
            }
        ],
    )

    refinado = HubDataAnalyst()._agregar_grafica_si_aplica(resultado, _tarea_grafica())

    assert refinado.grafica == png["png"]
    assert refinado.grafica_especificacion == png["especificacion"]


def test_hub_no_genera_grafica_si_no_se_pidio():
    resultado = ResultadoTarea(
        tarea_id="sql-1",
        estado=EstadoResultado.OK,
        respuesta="Hay 5,000 clientes.",
        datos={
            "columnas": ["segmento", "clientes"],
            "filas": [["Banca masiva", 3750]],
            "truncado": False,
        },
    )

    refinado = HubDataAnalyst()._agregar_grafica_si_aplica(resultado, _tarea())

    assert refinado.grafica is None
