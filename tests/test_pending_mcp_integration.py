"""Suite integrada: cinco servidores STDIO y orquestador real con Llama 3.1.

Ejecutar desde la raiz:
    .venv/Scripts/python.exe -m unittest discover -s tests -p test_pending_mcp_integration.py -v

Solo en RAG se sustituye el recuperador dentro del subproceso servidor.
El transporte, la herramienta, el formateo de evidencia y el LLM son reales.
No se verifica Pinecone, sus embeddings ni el corpus productivo.
"""

import asyncio
import importlib
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Solo afecta esta ejecucion y sus procesos hijos; no modifica .env.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.client.transports import StdioTransport
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_ollama import ChatOllama

from proyecto.agents import (
    data_steward_agent,
    data_analyst_agent,
    compliance_agent,
    document_intelligence_agent,
    normatividad_rag_agent,
)
from proyecto.agents.data_analyst_agent import DataAnalystAgent
from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend, MAX_FILAS, TABLAS_EXCLUIDAS

sys.path.insert(0, str(ROOT / "tests"))
from probar_data_analyst import base_sintetica_temporal, probar_seguridad
from proyecto.agents.compliance_agent import revisar_compliance_demo_mcp
from proyecto.agents.document_intelligence_agent import analizar_documento_demo_mcp
from proyecto.agents.normatividad_rag_agent import NormatividadRAGAgent
from proyecto.mcp_servers import mcp_client
from proyecto.memory.long_term import LongTermMemory, MemoryConfig
from proyecto.orchestration.orquestador import Orquestador, _evitar_delegacion_repetida


DEMOS = (
    (compliance_agent, revisar_compliance_demo_mcp, "revisar_compliance_demo"),
    (document_intelligence_agent, analizar_documento_demo_mcp, "analizar_documento_demo"),
)
RAG_MODULO = "proyecto.mcp_servers.normatividad_rag_server"
CONSULTA = "Demostracion de infraestructura MCP."


class RecuperadorDePrueba:
    """Evidencia sintetica, exclusivamente dentro del servidor RAG de prueba."""

    def __init__(self, k):
        self.k = k

    def invoke(self, query):
        return [Document(
            page_content=(
                "EVIDENCIA SINTETICA DE PRUEBA, NO NORMATIVA REAL. "
                f"El procedimiento de prueba requiere revision manual. k={self.k}"
            ),
            metadata={"source": "fixture_normativa.txt", "page": 2, "row": 7},
        )]


def ejecutar_servidor_rag_fixture():
    # Sustituye solo la dependencia externa en el subproceso real.
    NormatividadRAGAgent._get_retriever = lambda self: RecuperadorDePrueba(self.k)
    servidor = importlib.import_module(RAG_MODULO)
    servidor.mcp.run(show_banner=False)


TRANSPORTE_REAL = mcp_client.crear_transporte


def transporte_con_rag_fixture(modulo, *, env=None):
    if modulo == RAG_MODULO:
        return StdioTransport(
            command=sys.executable,
            args=[str(Path(__file__).resolve()), "--rag-fixture"],
            cwd=str(ROOT),
        )
    return TRANSPORTE_REAL(modulo, env=env)


class RegistroMCP(logging.Handler):
    """Observa llamadas completadas por el cliente real; no simula respuestas."""

    def __init__(self):
        super().__init__()
        self.llamadas = []

    def emit(self, record):
        if hasattr(record, "mcp_herramienta"):
            self.llamadas.append((record.mcp_modulo, record.mcp_herramienta))
            print(f"MCP CONFIRMADO: {record.mcp_modulo} -> {record.mcp_herramienta}", flush=True)


class RegistroHerramientas(BaseCallbackHandler):
    def on_tool_end(self, output, **kwargs):
        nombre = getattr(output, "name", "herramienta")
        contenido = getattr(output, "content", output)
        print(f"DEVOLUCION REAL DE HERRAMIENTA ({nombre}): {contenido}", flush=True)


class MCPIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        contexto = base_sintetica_temporal()
        cls.url_sintetica = contexto.__enter__()
        cls.addClassCleanup(contexto.__exit__, None, None, None)
        entorno = patch.dict(os.environ, {"DATABASE_URL": cls.url_sintetica})
        entorno.start()
        cls.addClassCleanup(entorno.stop)
        anterior = data_analyst_agent.url_db
        data_analyst_agent.conectar(cls.url_sintetica)
        cls.addClassCleanup(data_analyst_agent.conectar, anterior)
        print("BD DE PRUEBA: temporal, sintetica, generada con crear_bd.sql del equipo.", flush=True)

    def setUp(self):
        self.registro = RegistroMCP()
        self.logger = logging.getLogger(mcp_client.__name__)
        self.nivel_anterior = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.registro)
        self.addCleanup(self.logger.setLevel, self.nivel_anterior)
        self.addCleanup(self.logger.removeHandler, self.registro)

    def validar_demo(self, texto, agente, consulta=CONSULTA):
        datos = json.loads(texto)
        self.assertEqual(datos["agente"], agente.nombre)
        self.assertEqual(datos["estado"], "demostracion")
        self.assertEqual(datos["consulta"], consulta)
        self.assertTrue(datos["mensaje"])

    async def comprobar_demo(self, agente, herramienta, nombre):
        modulo = f"proyecto.mcp_servers.{agente.nombre}_server"
        async with Client(mcp_client.crear_transporte(modulo), timeout=60) as cliente:
            herramientas = await cliente.list_tools()
            self.assertEqual([item.name for item in herramientas], [nombre])
            self.assertEqual(herramientas[0].input_schema["required"], ["consulta"])
            resultado = await cliente.call_tool(nombre, {"consulta": CONSULTA})
            self.assertFalse(resultado.is_error)
            self.validar_demo(resultado.content[0].text, agente)
            for argumentos in ({"consulta": "  "}, {}):
                with self.assertRaises(ToolError):
                    await cliente.call_tool(nombre, argumentos)
        self.validar_demo(await herramienta.ainvoke({"consulta": CONSULTA}), agente)
        self.assertIn((modulo, nombre), self.registro.llamadas)
        print(f"STDIO REAL: {agente.nombre} -> {nombre}; descubrimiento, validacion y cliente async OK", flush=True)

    def test_00_guardia_repeticion_solo_estado_sintetico(self):
        # Prueba unitaria de estado: NO representa llamadas reales a LLM ni MCP.
        llamada = {"name": "data_steward", "args": {"consulta": "duplicados"}, "id": "anterior"}
        historial = [
            HumanMessage(content="consulta"),
            AIMessage(content="", tool_calls=[llamada]),
            ToolMessage(content="0 duplicados exactos", tool_call_id="anterior", name="data_steward"),
        ]
        repetida = AIMessage(id="actual", content="", tool_calls=[{**llamada, "id": "nueva"}])
        salida = _evitar_delegacion_repetida.after_model({"messages": historial + [repetida]}, None)
        self.assertEqual(salida["jump_to"], "end")
        self.assertEqual(salida["messages"][0].content, "0 duplicados exactos")
        self.assertEqual(salida["messages"][0].id, "actual")
        self.assertFalse(salida["messages"][0].tool_calls)
        nueva = AIMessage(content="", tool_calls=[{**llamada, "args": {"consulta": "estructura"}}])
        self.assertIsNone(_evitar_delegacion_repetida.after_model({"messages": historial + [nueva]}, None))
        mixta = AIMessage(content="", tool_calls=[
            {**llamada, "id": "repetida"},
            {"name": "compliance", "args": {"consulta": "KYC"}, "id": "adicional"},
        ])
        self.assertIsNone(_evitar_delegacion_repetida.after_model({"messages": historial + [mixta]}, None))
        otro_turno = historial + [HumanMessage(content="repite la consulta"), repetida]
        self.assertIsNone(_evitar_delegacion_repetida.after_model({"messages": otro_turno}, None))
        error = ToolMessage(content="fallo", status="error", tool_call_id="anterior")
        self.assertIsNone(_evitar_delegacion_repetida.after_model({"messages": historial[:2] + [error, repetida]}, None))

    def test_01_servidores_demostrativos_restantes(self):
        for agente, herramienta, nombre in DEMOS:
            with self.subTest(agente=agente.nombre):
                asyncio.run(self.comprobar_demo(agente, herramienta, nombre))
                self.assertEqual(agente.herramientas, [herramienta.name])
                self.assertEqual(agente.modelo, "llama3.1:latest")
                self.assertIn(agente.nombre, repr(agente))

    def test_02_normatividad_stdio_con_recuperador_sintetico(self):
        async def comprobar():
            async with Client(transporte_con_rag_fixture(RAG_MODULO), timeout=60) as cliente:
                herramientas = await cliente.list_tools()
                self.assertEqual([t.name for t in herramientas], ["buscar_normatividad"])
                self.assertIn("query", herramientas[0].input_schema["properties"])
            agente = NormatividadRAGAgent(k=2)
            texto = await agente.herramientas[0].ainvoke({"query": "procedimiento de prueba"})
            for fragmento in ("FUENTE: fixture_normativa.txt", "2", "7", "k=2", "EVIDENCIA SINTETICA"):
                self.assertIn(fragmento, texto)
            return texto

        with patch.object(mcp_client, "crear_transporte", new=transporte_con_rag_fixture):
            texto = asyncio.run(comprobar())
            texto_sync = normatividad_rag_agent.herramientas[0].invoke({"query": "procedimiento de prueba"})
            self.assertIn("fixture_normativa.txt", texto_sync)
            self.assertIn("k=3", texto_sync)
        self.assertIn((RAG_MODULO, "buscar_normatividad"), self.registro.llamadas)
        print("STDIO REAL RAG, RECUPERADOR SINTETICO: buscar_normatividad, evidencia y k=2 OK; Pinecone NO verificado.", flush=True)

    def test_03_sql_real_por_stdio_y_pruebas_del_equipo(self):
        modulo = "proyecto.mcp_servers.data_analyst_server"
        agente = DataAnalystAgent(self.url_sintetica)
        herramientas = {t.name: t for t in agente._tools}
        esperadas = {"listar_tablas", "describir_tabla", "valores_columna", "ejecutar_consulta"}
        self.assertEqual(set(agente.herramientas), esperadas)
        self.assertEqual(agente.modelo, "qwen2.5:7b")

        async def comprobar():
            async with Client(mcp_client.crear_transporte(modulo, env={"DATABASE_URL": self.url_sintetica}), timeout=60) as cliente:
                tools = await cliente.list_tools()
                self.assertEqual({t.name for t in tools}, esperadas)
                for t in tools:
                    self.assertNotIn("url_db", t.input_schema.get("properties", {}))
                casos = (
                    ("listar_tablas", {}),
                    ("describir_tabla", {"tabla": "vw_cliente_publico"}),
                    ("valores_columna", {"tabla": "vw_cliente_publico", "columna": "segmento"}),
                    ("ejecutar_consulta", {"sql": "SELECT COUNT(*) AS total FROM dim_cliente"}),
                )
                resultados = {}
                for nombre, argumentos in casos:
                    r = await cliente.call_tool(nombre, argumentos)
                    self.assertFalse(r.is_error)
                    resultados[nombre] = json.loads(r.content[0].text)
                    self.assertNotIn("error", resultados[nombre])
                    print(f"STDIO SQL REAL: {nombre} OK", flush=True)
                self.assertEqual(resultados["listar_tablas"]["motor"], "sqlite")
                self.assertFalse(TABLAS_EXCLUIDAS & set(resultados["listar_tablas"]["tablas"]))
                self.assertEqual(resultados["describir_tabla"]["filas"], 5000)
                self.assertEqual(len(resultados["valores_columna"]["valores"]), 3)
                self.assertEqual(resultados["ejecutar_consulta"]["filas"], [[5000]])
            for nombre, argumentos in casos:
                datos = json.loads(await herramientas[nombre].ainvoke(argumentos))
                self.assertNotIn("error", datos)
            with self.assertRaises(ToolError):
                await mcp_client.llamar_herramienta_mcp(modulo, "describir_tabla", {}, env={"DATABASE_URL": self.url_sintetica})

        asyncio.run(comprobar())
        self.assertTrue(esperadas <= {n for m, n in self.registro.llamadas if m == modulo})
        probar_seguridad(agente)  # Las seis pruebas originales del equipo, ahora a traves de MCP real.
        ejecutar = herramientas["ejecutar_consulta"]
        def sql(consulta):
            datos = json.loads(ejecutar.invoke({"sql": consulta}))
            self.assertNotIn("error", datos)
            return datos

        segmentos = sql("SELECT segmento, COUNT(*) AS total FROM vw_cliente_publico GROUP BY segmento")
        self.assertEqual(dict(segmentos["filas"]), {"Banca masiva": 3750, "Banca preferente": 1000, "Banca patrimonial": 250})
        retiros = sql("SELECT substr(fec_txn,1,7) AS mes, ROUND(SUM(saldo_txn),2) AS total FROM vw_txn_firmada WHERE desc_txn = 'RETIRO' GROUP BY mes ORDER BY mes")
        self.assertEqual([r[1] for r in retiros["filas"]], [3750997.60, 3826017.52, 3864277.87, 3902920.41, 3941949.50])
        ranking = sql("SELECT cve_producto, ROUND(SUM(saldo_mes),2) AS total FROM vw_saldo_mensual WHERE anio_mes = 202606 GROUP BY cve_producto ORDER BY total DESC")
        self.assertEqual(ranking["filas"][0], ["C_PERSONAL", 379302849.74])
        promedios = sql("SELECT cve_producto, ROUND(AVG(saldo_mes),2) AS promedio FROM vw_saldo_mensual WHERE anio_mes = 202606 GROUP BY cve_producto")
        self.assertEqual(dict(promedios["filas"]), {"AHORRO": 5311.94, "CHEQUES": 10613.53, "C_PERSONAL": 75860.57, "TDC": 73058.99})
        limitado = sql("SELECT num_cte FROM dim_cliente ORDER BY num_cte")
        self.assertEqual(len(limitado["filas"]), MAX_FILAS)
        self.assertTrue(limitado["truncado"])
        self.assertIn("error", json.loads(ejecutar.invoke({"sql": "SELECT 'sintetico' AS correo"})))
        self.assertIn("error", json.loads(herramientas["valores_columna"].invoke({"tabla": "vw_cliente_publico", "columna": "correo"})))
        self.assertIn("error", json.loads(herramientas["describir_tabla"].invoke({"tabla": "raw_clientes"})))
        backend = DataAnalystSQLBackend(self.url_sintetica)
        try:
            from sqlalchemy.exc import DBAPIError
            with backend._get_engine().connect() as conexion:
                self.assertEqual(conexion.exec_driver_sql("PRAGMA query_only").scalar(), 1)
                with self.assertRaises(DBAPIError):
                    conexion.exec_driver_sql("DELETE FROM dim_cliente")
        finally:
            backend._engine.dispose()
        # Comprueba conectar() sin compartir motores entre procesos ni alterar el entorno padre.
        with tempfile.TemporaryDirectory(prefix="ProyectoFinalFinal_reconexion_") as directorio:
            destino = Path(directorio) / "otra.db"
            conexion = sqlite3.connect(destino)
            try:
                conexion.executescript("CREATE TABLE marcador(n INTEGER); INSERT INTO marcador VALUES (7);")
                conexion.commit()
            finally:
                conexion.close()
            agente.conectar("sqlite:///" + destino.as_posix())
            self.assertEqual(sql("SELECT n FROM marcador")["filas"], [[7]])
            self.assertEqual(os.environ["DATABASE_URL"], self.url_sintetica)
            agente.conectar(self.url_sintetica)
            self.assertEqual(sql("SELECT COUNT(*) FROM dim_cliente")["filas"], [[5000]])
        agente.conectar("")
        sin_configurar = json.loads(herramientas["listar_tablas"].invoke({}))
        self.assertIn("error", sin_configurar)
        print("SQL REAL OK: cuatro herramientas, seis pruebas del equipo, cuatro resultados numericos, privacidad, limite de filas, solo lectura y reconexion.", flush=True)

    def test_04_orquestador_sql_y_agentes_extremo_a_extremo(self):
        # El fallo de disponibilidad se informa como fallo real, nunca como exito simulado.
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10) as response:
            modelos = json.load(response)["models"]
        disponibles = [modelo["name"] for modelo in modelos]
        self.assertIn("llama3.1:latest", disponibles)
        original_modelo = data_analyst_agent.modelo
        modelo_sql = "qwen2.5:7b" if "qwen2.5:7b" in disponibles else "llama3.1:latest"
        data_analyst_agent.modelo = modelo_sql
        data_analyst_agent._agente = None
        self.addCleanup(setattr, data_analyst_agent, "modelo", original_modelo)
        self.addCleanup(setattr, data_analyst_agent, "_agente", None)
        print(f"LLM SQL REAL DE PRUEBA: {modelo_sql}; predeterminado del equipo: {original_modelo}", flush=True)
        casos = (
            (
                "data_steward",
                "Cuantas filas y columnas tiene el CSV sintetico? Inspecciona su "
                "estructura y comunica solo los conteos agregados.",
                {"inspeccionar_dataset"},
            ),
            (
                "data_steward",
                "Revisa cuantos duplicados exactos hay en el CSV sintetico. "
                "Consulta una vez la herramienta de calidad y comunica el conteo agregado.",
                {"validar_calidad_datos"},
            ),
            (
                "data_analyst",
                "En la vista SQL vw_cliente_publico de la base bancaria, calcula COUNT(*) "
                "agrupado por segmento. Consulta su esquema si lo necesitas y ejecuta "
                "la consulta SQL real. Devuelve los tres segmentos y sus conteos.",
                {"ejecutar_consulta"},
            ),
            (
                "compliance",
                "Necesito una revision de cumplimiento KYC y AML para el reporte al regulador. "
                "Ejecuta la herramienta disponible y comunica su resultado aunque sea demostrativo.",
                {"revisar_compliance_demo"},
            ),
            (
                "document_intelligence",
                "Necesito lectura OCR y extraccion de datos de una factura escaneada. "
                "Ejecuta la herramienta disponible y comunica su resultado aunque sea demostrativo.",
                {"analizar_documento_demo"},
            ),
        )
        def llm_real_acotado(**kwargs):
            # Inferencia real, con limite de salida y tiempo para evitar ejecuciones indefinidas.
            return ChatOllama(**kwargs, num_predict=384, client_kwargs={"timeout": 90})

        def llm_sql_real(**kwargs):
            return ChatOllama(**kwargs, num_predict=512, num_ctx=4096, client_kwargs={"timeout": 90})

        with (
            patch.object(mcp_client, "crear_transporte", new=transporte_con_rag_fixture),
            patch("proyecto.orchestration.orquestador.ChatOllama", new=llm_real_acotado),
            patch("proyecto.agents.data_steward_agent.ChatOllama", new=llm_real_acotado),
            patch("proyecto.agents.data_analyst_agent.ChatOllama", new=llm_sql_real),
        ):
            # SQL usa una vista conocida: prueba el transporte y procesar(), no
            # certifica el descubrimiento libre de esquema con el modelo suplente.
            # Primero SQL y los dos demostrativos; despues Data Steward.
            for esperado, consulta, herramientas in casos[2:] + casos[:2]:
                with self.subTest(agente=esperado):
                    self.registro.llamadas.clear()
                    # Memoria real desactivada por configuracion publica para no escribir datos.
                    orquestador = Orquestador(memory=LongTermMemory(config=MemoryConfig(enabled=False)))
                    # Limites de prueba para informar bucles reales del modelo como errores.
                    # No se sustituye el LLM, el agente ni sus herramientas.
                    orquestador._agente = orquestador._get_agente().with_config({
                        "recursion_limit": 12, "callbacks": [RegistroHerramientas()],
                    })
                    data_steward_agent._agent = data_steward_agent._get_agent().with_config({"recursion_limit": 12})
                    print(f"E2E REAL iniciando: {esperado} (seleccion automatica Llama 3.1)", flush=True)
                    respuesta = orquestador.procesar(consulta)
                    print(f"RESPUESTA REAL ({esperado}): {respuesta}", flush=True)
                    self.assertTrue(respuesta.strip())
                    if esperado == "data_steward":
                        palabra = "filas" if "inspeccionar_dataset" in herramientas else "duplicad"
                        self.assertIn(palabra, respuesta.casefold())
                        self.assertNotIn('"name":', respuesta)
                    self.assertIn(esperado, orquestador.agentes_invocados)
                    self.assertEqual(set(orquestador.agentes_invocados), {esperado})
                    modulo = f"proyecto.mcp_servers.{esperado}_server"
                    completadas = {nombre for servidor, nombre in self.registro.llamadas if servidor == modulo}
                    self.assertTrue(
                        herramientas <= completadas,
                        f"STDIO completadas={self.registro.llamadas}; respuesta={respuesta}",
                    )
                    mensajes_tool = [m for m in orquestador._mensajes if isinstance(m, ToolMessage)]
                    self.assertTrue(mensajes_tool)
                    for mensaje in mensajes_tool:
                        self.assertNotEqual(getattr(mensaje, "status", None), "error")
                        self.assertNotIn("Error al ejecutar el agente", str(mensaje.content))
                    if esperado == "data_analyst":
                        self.assertFalse(any("demostracion" in str(m.content) for m in mensajes_tool))
                        self.assertTrue(any("3750" in str(m.content).replace(",", "").replace(".", "").replace(" ", "") for m in mensajes_tool))
                    if esperado in {a.nombre for a, _, _ in DEMOS}:
                        # Este mensaje es la devolucion real de procesar a la herramienta del orquestador.
                        for mensaje in mensajes_tool:
                            if mensaje.name == esperado:
                                datos = json.loads(mensaje.content)
                                self.assertEqual(datos["agente"], esperado)
                                self.assertEqual(datos["estado"], "demostracion")
                        ultima = next(m for m in reversed(mensajes_tool) if m.name == esperado)
                        self.assertEqual(json.loads(respuesta), json.loads(ultima.content))
                    print(
                        f"E2E OK: agentes={orquestador.agentes_invocados}; "
                        f"MCP STDIO completadas={self.registro.llamadas}; respuesta={respuesta}",
                        flush=True,
                    )
        print("RAG: cliente y herramienta MCP por STDIO real con recuperador sintetico; Pinecone, embeddings y corpus real pendientes.", flush=True)


if __name__ == "__main__":
    if "--rag-fixture" in sys.argv:
        ejecutar_servidor_rag_fixture()
    else:
        unittest.main()
