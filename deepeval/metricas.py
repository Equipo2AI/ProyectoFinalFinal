"""Métricas de la suite y modelo juez.

Métricas disponibles (campo ``metricas`` de cada caso):

- ``reglas``      Determinista, sin LLM: agente y herramientas esperadas, textos
                  que deben o no aparecer y ausencia de errores. Siempre corre.
- ``relevancia``  ¿La respuesta atiende la pregunta? (AnswerRelevancyMetric)
- ``fidelidad``   ¿La respuesta se apoya en lo que devolvieron las herramientas?
                  Detecta alucinaciones. (FaithfulnessMetric)
- ``correccion``  ¿Coincide con ``respuesta_esperada``? (G-Eval)
- ``criterio``    Criterio libre escrito en el YAML. (G-Eval)

El juez se elige con ``DEEPEVAL_JUEZ`` = ``proveedor:modelo``:
    ollama:qwen2.5:7b-instruct   (por defecto, local)
    openai:gpt-4.1-mini          (requiere OPENAI_API_KEY)
    gemini:gemini-2.5-flash      (requiere GOOGLE_API_KEY)
"""

from __future__ import annotations

import os
import unicodedata
from functools import lru_cache

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BaseMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase

try:  # deepeval >= 4
    from deepeval.test_case import SingleTurnParams as Params
except ImportError:  # deepeval 3.x
    from deepeval.test_case import LLMTestCaseParams as Params

from agente import ResultadoAgente
from cargador import Caso

JUEZ_POR_DEFECTO = "ollama:qwen2.5:7b-instruct"


@lru_cache(maxsize=1)
def modelo_juez():
    proveedor, _, nombre = os.getenv("DEEPEVAL_JUEZ", JUEZ_POR_DEFECTO).partition(":")
    proveedor = proveedor.strip().lower()
    if not nombre:
        raise ValueError("DEEPEVAL_JUEZ debe tener la forma proveedor:modelo")
    if proveedor == "ollama":
        from deepeval.models import OllamaModel

        return OllamaModel(
            model=nombre,
            base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            temperature=0,
        )
    if proveedor == "openai":
        from deepeval.models import GPTModel

        return GPTModel(model=nombre, temperature=0)
    if proveedor == "gemini":
        from deepeval.models import GeminiModel

        return GeminiModel(
            model=nombre,
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
            temperature=0,
        )
    raise ValueError(f"Proveedor de juez no soportado: {proveedor!r}")


def _plano(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar textos sin falsos negativos."""
    normalizado = unicodedata.normalize("NFKD", str(texto).casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


class ReglasMetric(BaseMetric):
    """Verificaciones deterministas definidas en el YAML del caso."""

    def __init__(self, caso: Caso, resultado: ResultadoAgente):
        self.caso = caso
        self.resultado = resultado
        self.threshold = 1.0
        self.async_mode = False
        self.include_reason = True
        self.evaluation_model = None

    def _fallos(self) -> tuple[list[str], int]:
        caso, res = self.caso, self.resultado
        respuesta = _plano(res.respuesta)
        fallos: list[str] = []
        total = 1
        if res.errores:
            fallos.append("el agente reportó errores: " + " | ".join(res.errores)[:400])
        if not res.respuesta.strip():
            fallos.append("la respuesta está vacía")

        if caso.agente_esperado:
            total += 1
            if caso.agente_esperado not in res.agentes:
                fallos.append(
                    f"agente esperado {caso.agente_esperado!r}; participaron {res.agentes}"
                )
        for herramienta in caso.herramientas_esperadas:
            total += 1
            if herramienta not in res.herramientas:
                fallos.append(
                    f"no se llamó {herramienta!r}; se llamaron {res.herramientas}"
                )
        for texto in caso.debe_contener:
            total += 1
            if _plano(texto) not in respuesta:
                fallos.append(f"la respuesta no contiene {texto!r}")
        if caso.debe_contener_alguno:
            total += 1
            if not any(_plano(t) in respuesta for t in caso.debe_contener_alguno):
                fallos.append(
                    f"la respuesta no contiene ninguno de {list(caso.debe_contener_alguno)}"
                )
        for texto in caso.no_debe_contener:
            total += 1
            if _plano(texto) in respuesta:
                fallos.append(f"la respuesta contiene texto prohibido {texto!r}")
        return fallos, total

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        fallos, total = self._fallos()
        self.score = max(0.0, 1 - len(fallos) / total)
        self.success = not fallos
        self.reason = "; ".join(fallos) if fallos else "Todas las reglas se cumplen."
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self):
        return "Reglas"


def _metricas_solicitadas(caso: Caso) -> list[str]:
    """Las del caso; si no declara, se deducen de los campos que llenó."""
    nombres = list(caso.metricas)
    if not nombres:
        nombres = ["relevancia"]
    if caso.respuesta_esperada and "correccion" not in nombres:
        nombres.append("correccion")
    if caso.criterio and "criterio" not in nombres:
        nombres.append("criterio")
    return [n for n in dict.fromkeys(nombres) if n != "reglas"]


def construir_metricas(caso: Caso, resultado: ResultadoAgente) -> list[BaseMetric]:
    metricas: list[BaseMetric] = [ReglasMetric(caso, resultado)]
    if os.getenv("DEEPEVAL_SOLO_REGLAS", "").strip().lower() in {"1", "true", "si", "sí"}:
        return metricas

    for nombre in _metricas_solicitadas(caso):
        if nombre == "relevancia":
            metricas.append(
                AnswerRelevancyMetric(threshold=caso.umbral, model=modelo_juez())
            )
        elif nombre == "fidelidad":
            # Sin salidas de herramientas no hay evidencia contra la cual medir.
            if resultado.contexto:
                metricas.append(
                    FaithfulnessMetric(threshold=caso.umbral, model=modelo_juez())
                )
        elif nombre == "correccion":
            if not caso.respuesta_esperada:
                raise ValueError(
                    f"{caso.id}: la métrica 'correccion' necesita 'respuesta_esperada'"
                )
            metricas.append(
                GEval(
                    name="Corrección",
                    criteria=(
                        "Determina si la respuesta obtenida (actual output) comunica "
                        "los mismos hechos que la respuesta esperada (expected "
                        "output). Penaliza cifras, nombres o conclusiones distintas "
                        "y omisiones de datos clave. No penalices diferencias de "
                        "redacción, formato ni información adicional correcta."
                    ),
                    evaluation_params=[
                        Params.INPUT,
                        Params.ACTUAL_OUTPUT,
                        Params.EXPECTED_OUTPUT,
                    ],
                    threshold=caso.umbral,
                    model=modelo_juez(),
                )
            )
        elif nombre == "criterio":
            if not caso.criterio:
                raise ValueError(f"{caso.id}: la métrica 'criterio' necesita 'criterio'")
            metricas.append(
                GEval(
                    name="Criterio",
                    criteria=caso.criterio,
                    evaluation_params=[Params.INPUT, Params.ACTUAL_OUTPUT],
                    threshold=caso.umbral,
                    model=modelo_juez(),
                )
            )
    return metricas
