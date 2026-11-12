"""Pruebas del puente opcional entre el grafo y HubDataAnalyst."""

from __future__ import annotations

import importlib

from proyecto.orchestration.tareas import (
    EstadoResultado,
    ResultadoTarea,
)

grafo = importlib.import_module("proyecto.orchestration.grafo")


class HubFalso:
    def ejecutar(self, tarea, contexto):
        assert tarea.instruccion == "¿Cuántos clientes hay?"
        assert contexto.perfil == {"rol": "usuario", "cliente_id": 1}
        return ResultadoTarea(
            tarea_id=tarea.id,
            estado=EstadoResultado.OK,
            respuesta="Hay 5,000 clientes.",
            sql="SELECT COUNT(*) FROM dim_cliente",
            datos={"columnas": ["COUNT(*)"], "filas": [[5000]]},
            trazas=[
                {
                    "name": "ejecutar_consulta",
                    "args": {"sql": "SELECT COUNT(*) FROM dim_cliente"},
                    "salida": '{"columnas": ["COUNT(*)"], "filas": [[5000]]}',
                    "estado": "ok",
                }
            ],
            fuentes=["banamex.db"],
        )


def test_grafo_puede_usar_hub_data_analyst(monkeypatch):
    monkeypatch.setenv("DATA_ANALYST_HUB", "1")
    monkeypatch.setattr(grafo, "_get_hub_data_analyst", lambda: HubFalso())

    resultado = grafo._nodo_data_analyst(
        {
            "consulta": "¿Cuántos clientes hay?",
            "perfil": {"rol": "usuario", "cliente_id": 1},
        }
    )

    datos = resultado["resultados"]["data_analyst"]
    assert datos["estado"] == "ok"
    assert datos["sql"] == "SELECT COUNT(*) FROM dim_cliente"
    assert datos["evidencia"] == {}
    assert resultado["errores"] == []
    assert resultado["trazas"][0]["name"] == "ejecutar_consulta"


def test_grafo_reporta_error_del_hub(monkeypatch):
    class HubError:
        def ejecutar(self, tarea, contexto):
            return ResultadoTarea(
                tarea_id=tarea.id,
                estado=EstadoResultado.ERROR,
                error="La consulta no pasó la validación.",
            )

    monkeypatch.setenv("DATA_ANALYST_HUB", "1")
    monkeypatch.setattr(grafo, "_get_hub_data_analyst", lambda: HubError())

    resultado = grafo._nodo_data_analyst(
        {
            "consulta": "consulta inválida",
            "perfil": {},
        }
    )

    assert resultado["resultados"]["data_analyst"]["estado"] == "error"
    assert "validación" in resultado["errores"][0]


# Estas son pruebas de control del flujo con dobles: no se presentan como
# inferencias ni llamadas MCP reales. Los spies fallan si se intenta inferir.
import pytest
from unittest.mock import Mock
from proyecto.orchestration import orquestador


@pytest.mark.parametrize('modo', ['graph', 'hub'])
def test_fuera_de_alcance_termina_antes_de_memoria_modelo_o_mcp(monkeypatch, identidad_prueba, modo):
    memoria = Mock()
    instancia = orquestador.Orquestador(memory=memoria)
    instancia.modo = modo
    instancia.ultimas_herramientas = [{'name':'anterior'}]
    instancia.agentes_invocados = ['anterior']
    prohibido = Mock(side_effect=AssertionError('No debe invocar un recurso externo'))
    monkeypatch.setattr(orquestador, 'ChatOllama', prohibido)
    monkeypatch.setattr(grafo, 'ChatOllama', prohibido)
    monkeypatch.setattr(orquestador, '_llamar_mcp', prohibido)
    monkeypatch.setattr(grafo, '_llamar_mcp', prohibido)
    monkeypatch.setattr(instancia, '_procesar_grafo', prohibido)
    monkeypatch.setattr(instancia, '_procesar_hub', prohibido)
    pregunta = 'Por qu\u00e9 el cielo es azul?'
    respuesta = instancia.procesar(pregunta)
    assert respuesta == grafo.RESPUESTA_FUERA_DE_ALCANCE
    assert instancia._mensajes[-1].content == respuesta
    assert instancia._mensajes[-2].content == pregunta
    assert instancia.ultimas_herramientas == [] and instancia.agentes_invocados == []
    prohibido.assert_not_called()
    memoria.prepare.assert_not_called()
    memoria.update.assert_not_called()


@pytest.mark.parametrize('consulta', ['Por qu\u00e9 el cielo es azul?', 'Cuantos planetas hay?', 'Dame una receta de pizza'])
def test_grafo_no_despacha_recursos_para_tema_explicitamente_ajeno(monkeypatch, consulta):
    prohibido = Mock(side_effect=AssertionError('Inferencia o MCP innecesario'))
    monkeypatch.setattr(grafo, 'ChatOllama', prohibido)
    monkeypatch.setattr(grafo, '_llamar_mcp', prohibido)
    resultado = grafo.ejecutar_grafo(consulta, ultimo_agente='data_analyst')
    assert resultado['respuesta'] == grafo.RESPUESTA_FUERA_DE_ALCANCE
    assert not resultado['trazas']
    prohibido.assert_not_called()


@pytest.mark.parametrize('consulta,opciones', [
    ('Hola, buenos dias', {}),
    ('Y en junio?', {'hay_contexto':True}),
    ('Explica eso de los planetas', {'hay_contexto':True}),
    ('Por que es azul?', {}),
    ('Explica como funciona', {}),
    ('Que efecto tiene el clima sobre el riesgo de credito?', {}),
    ('Calcula el costo de preparar una pizza para mi presupuesto', {}),
    ('Que significa el azul de la grafica de saldos?', {'hay_contexto':True}),
    ('Por que el cielo es azul?', {'imagen_pendiente':'comprobante.png'}),
])
def test_alcance_conserva_saludos_ambiguedad_contexto_y_lenguaje_financiero(consulta, opciones):
    assert not grafo.fuera_de_alcance(consulta, **opciones)


@pytest.mark.parametrize('consulta,servidor,herramienta', [
    ('Que cuentas tengo contratadas?', 'data_analyst_server','analizar_datos'),
    ('Que dice el aviso de privacidad del banco?', 'normatividad_rag_server','buscar_normatividad'),
])
def test_consultas_del_dominio_siguen_llegando_al_especialista(monkeypatch, identidad_prueba, consulta, servidor, herramienta):
    memoria = Mock()
    memoria.prepare.side_effect = lambda mensajes,q: [{'role':'user','content':q}]
    instancia = orquestador.Orquestador(memory=memoria)
    instancia.modo = 'graph'
    dispatcher = Mock(return_value='Respuesta de contrato de prueba, no evidencia SQL o documental real.')
    no_modelo = Mock(side_effect=AssertionError('La ruta especialista no necesita respuesta directa'))
    monkeypatch.delenv('DATA_ANALYST_HUB', raising=False)
    monkeypatch.setattr(grafo, '_llamar_mcp', dispatcher)
    monkeypatch.setattr(grafo, 'ChatOllama', no_modelo)
    assert not grafo.fuera_de_alcance(consulta)
    instancia.procesar(consulta)
    assert dispatcher.call_count == 1
    args = dispatcher.call_args.args
    assert args[:2] == (servidor,herramienta)
    no_modelo.assert_not_called()
