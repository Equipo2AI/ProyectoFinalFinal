"""US-09 · Inspeccionar cómo se generó una respuesta (P0).

Se reemplaza el orquestador por uno falso con trazas conocidas para probar el
contrato que el servicio entrega a la UI (pasos, SQL, fuentes, alertas).
"""

from __future__ import annotations

import pytest

import proyecto.servicio as servicio
from proyecto.security import local_auth


class OrquestadorFalso:
    modelo = "falso"
    modo_efectivo = "grafo"
    imagen_pendiente = None

    def __init__(self, trazas, respuesta="Respuesta", estado=None, al_procesar=None):
        self.trazas = trazas
        self.respuesta = respuesta
        self.estado = estado or {}
        self.al_procesar = al_procesar
        self.ultimas_herramientas: list = []
        self.agentes_invocados: list = []
        self.ultimo_estado_grafo: dict = {}

    def procesar(self, consulta, perfil=None):
        if self.al_procesar:
            self.al_procesar()
        self.ultimas_herramientas = self.trazas
        self.ultimo_estado_grafo = self.estado
        return self.respuesta


@pytest.fixture
def con_orquestador(operador, monkeypatch):
    def _usar(orquestador, identidad="analista"):
        monkeypatch.setattr(servicio, "_get_orquestador", lambda: orquestador)
        token = local_auth.login(identidad, operador.claves[identidad])
        monkeypatch.setenv("PROYECTO_AUTH_TOKEN", token)
        return orquestador

    return _usar


def test_ca01_ca02_traza_identifica_herramienta_estado_y_sql(con_orquestador):
    sql = "SELECT cve_producto, COUNT(*) FROM dim_cuenta GROUP BY cve_producto"
    con_orquestador(OrquestadorFalso(
        [{"name": "mcp_analizar_datos", "args": {"consulta": "cuentas"}, "estado": "ok",
          "salida": f"Resultado\n```sql\n{sql}\n```"}],
        estado={"resultados": {"data_analyst": {
            "sql": sql, "datos": {"columnas": ["cve_producto", "n"], "filas": [["AHORRO", 5000]]},
        }}},
    ))
    respuesta = servicio.preguntar_orquestador("¿Cuántas cuentas hay por producto?")
    assert not respuesta.get("error")
    assert respuesta["agentes"] == ["data_analyst"]
    paso = respuesta["pasos"][0]
    assert paso["agente"] == "data_analyst" and paso["estado"] == "ok"
    assert paso["detalle"].startswith("mcp_analizar_datos")
    assert respuesta["sql"] == sql
    assert respuesta["datos"] == [{"cve_producto": "AHORRO", "n": 5000}]


def test_ca01_herramienta_con_error_queda_visible(con_orquestador):
    con_orquestador(OrquestadorFalso(
        [{"name": "mcp_inspeccionar_dataset", "args": {}, "estado": "error", "salida": "Timeout"}]
    ))
    respuesta = servicio.preguntar_orquestador("Inspecciona raw_clientes")
    assert respuesta["pasos"][0]["estado"] == "error"
    assert any("mcp_inspeccionar_dataset falló" in a for a in respuesta["alertas"])


def test_ca03_fuentes_de_la_evidencia_normativa(con_orquestador):
    con_orquestador(OrquestadorFalso(
        [{"name": "mcp_buscar_normatividad", "args": {"query": "q"}, "estado": "ok",
          "salida": "FUENTE: Aviso legal citibanamex.txt, página 1\nTexto literal."}]
    ), identidad="cliente_1")
    respuesta = servicio.preguntar_orquestador("¿Qué dice el aviso legal?")
    assert respuesta["fuentes"] == ["Aviso legal citibanamex.txt, página 1"]
    assert respuesta["agentes"] == ["normatividad_rag"]


def test_ca02_la_respuesta_analitica_redacta_pii(con_orquestador):
    con_orquestador(OrquestadorFalso(
        [{"name": "mcp_analizar_datos", "args": {}, "estado": "ok", "salida": "ok"}],
        respuesta="El contacto es ana@example.com",
        estado={"resultados": {"data_analyst": {"sql": "SELECT 1"}}},
    ))
    respuesta = servicio.preguntar_orquestador("¿Cuántas cuentas hay?")
    assert "ana@example.com" not in respuesta["respuesta"]
    assert any("Se ocultaron datos personales" in a for a in respuesta["alertas"])


def test_ca04_turno_autenticado_no_se_traza_en_langsmith(con_orquestador):
    from langsmith.run_helpers import get_tracing_context

    estados = []
    con_orquestador(OrquestadorFalso(
        [], al_procesar=lambda: estados.append(get_tracing_context().get("enabled"))
    ))
    servicio.preguntar_orquestador("¿Cuántas cuentas hay por producto?")
    assert estados == [False]


def test_ca04_los_subprocesos_mcp_no_trazan(operador):
    with operador.sesion("cliente_1"):
        entorno = local_auth.subprocess_env()
    assert entorno["LANGSMITH_TRACING"] == "false"
    assert entorno["LANGCHAIN_TRACING_V2"] == "false"
