"""Suite de evaluación del agente: un test por cada caso de ``casos/*.yaml``.

    uv run deepeval test run deepeval/test_agente.py

No hace falta editar este archivo para agregar, cambiar o borrar preguntas.

Cada caso guarda en ``metadata`` de DeepEval las historias que cubre y su
rendimiento (tiempos, CPU, memoria, tokens). El equipo y el resumen de la
corrida se escriben al final en ``resultados/rendimiento_<fecha>.json/.md``.
"""

from __future__ import annotations

import time

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, ToolCall

from agente import ejecutar
from cargador import Caso, cargar_casos
from metricas import construir_metricas, presupuesto_segundos
from rendimiento import CORRIDA


def _parametro(caso: Caso):
    marcas = [getattr(pytest.mark, etiqueta) for etiqueta in caso.etiquetas]
    if caso.skip:
        marcas.append(pytest.mark.skip(reason=caso.skip))
    if caso.xfail:
        marcas.append(pytest.mark.xfail(reason=caso.xfail, strict=False))
    return pytest.param(caso, id=caso.id, marks=marcas)


def _metadata(caso: Caso, rendimiento: dict) -> dict:
    return {
        "cubre": list(caso.cubre),
        "historias": list(caso.historias),
        "identidad": caso.identidad,
        "presupuesto_s": presupuesto_segundos(caso),
        "rendimiento": rendimiento,
        # El equipo va en cada caso para que el reporte HTML lo muestre sin
        # depender de archivos externos; es un diccionario pequeño.
        "equipo": CORRIDA.equipo_actual(),
    }


@pytest.mark.parametrize("caso", [_parametro(c) for c in cargar_casos()])
def test_caso(caso: Caso):
    registro = CORRIDA.caso(caso.id)
    registro.update({
        "historias": list(caso.historias),
        "cubre": list(caso.cubre),
        "identidad": caso.identidad,
    })

    resultado = ejecutar(caso)
    registro["rendimiento"] = resultado.rendimiento
    llm = (resultado.rendimiento or {}).get("llm") or {}

    test_case = LLMTestCase(
        name=caso.id,
        input=caso.pregunta or f"[adjunto sin instrucción: {caso.adjunto}]",
        actual_output=resultado.respuesta,
        expected_output=caso.respuesta_esperada,
        retrieval_context=resultado.contexto or None,
        tools_called=[ToolCall(name=n) for n in resultado.herramientas],
        expected_tools=[ToolCall(name=n) for n in caso.herramientas_esperadas] or None,
        completion_time=resultado.duracion,
        input_token_count=llm.get("tokens_entrada") or None,
        output_token_count=llm.get("tokens_salida") or None,
        tags=list(caso.etiquetas) or None,
        metadata=_metadata(caso, resultado.rendimiento),
    )
    inicio = time.perf_counter()
    try:
        # run_async=False: el juez local atiende una métrica a la vez.
        assert_test(test_case, construir_metricas(caso, resultado), run_async=False)
    finally:
        registro["s_evaluacion"] = round(time.perf_counter() - inicio, 3)
