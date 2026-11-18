"""Suite de evaluación del agente: un test por cada caso de ``casos/*.yaml``.

    uv run deepeval test run deepeval/test_agente.py

No hace falta editar este archivo para agregar, cambiar o borrar preguntas.
"""

from __future__ import annotations

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, ToolCall

from agente import ejecutar
from cargador import Caso, cargar_casos
from metricas import construir_metricas


def _parametro(caso: Caso):
    marcas = [getattr(pytest.mark, etiqueta) for etiqueta in caso.etiquetas]
    if caso.skip:
        marcas.append(pytest.mark.skip(reason=caso.skip))
    if caso.xfail:
        marcas.append(pytest.mark.xfail(reason=caso.xfail, strict=False))
    return pytest.param(caso, id=caso.id, marks=marcas)


@pytest.mark.parametrize("caso", [_parametro(c) for c in cargar_casos()])
def test_caso(caso: Caso):
    resultado = ejecutar(caso)

    test_case = LLMTestCase(
        name=caso.id,
        input=caso.pregunta,
        actual_output=resultado.respuesta,
        expected_output=caso.respuesta_esperada,
        retrieval_context=resultado.contexto or None,
        tools_called=[ToolCall(name=n) for n in resultado.herramientas],
        expected_tools=[ToolCall(name=n) for n in caso.herramientas_esperadas] or None,
        completion_time=resultado.duracion,
        tags=list(caso.etiquetas) or None,
    )
    # run_async=False: el juez local atiende una métrica a la vez.
    assert_test(test_case, construir_metricas(caso, resultado), run_async=False)
