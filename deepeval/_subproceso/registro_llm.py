"""Registra cada llamada a un modelo de LangChain (tokens y tiempos de Ollama).

Solo se activa si ``DEEPEVAL_LLM_STATS_FILE`` apunta a un archivo: la suite lo
define por caso, así que cada caso tiene su propio registro. Cada llamada
agrega una línea JSON con lo que reporta Ollama (``eval_count``,
``eval_duration``, ``load_duration``…). No guarda prompts ni respuestas.

Se carga en el proceso de pytest y, mediante ``sitecustomize.py`` de esta
carpeta, en los subprocesos MCP (Data Steward, Data Analyst, Normatividad y
Document Intelligence corren su modelo ahí).
"""

from __future__ import annotations

import json
import os
import time

VARIABLE = "DEEPEVAL_LLM_STATS_FILE"
_NS = 1e9
_registrado = False


def _segundos(valor) -> float | None:
    try:
        return round(float(valor) / _NS, 4) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _info_generacion(generacion) -> dict:
    info: dict = {}
    mensaje = getattr(generacion, "message", None)
    for fuente in (
        getattr(generacion, "generation_info", None),
        getattr(mensaje, "response_metadata", None),
    ):
        if isinstance(fuente, dict):
            info.update({k: v for k, v in fuente.items() if v is not None})
    uso = getattr(mensaje, "usage_metadata", None) or {}
    if isinstance(uso, dict):
        info.setdefault("prompt_eval_count", uso.get("input_tokens"))
        info.setdefault("eval_count", uso.get("output_tokens"))
    return info


def _escribir(registro: dict) -> None:
    ruta = os.environ.get(VARIABLE, "").strip()
    if not ruta:
        return
    try:
        with open(ruta, "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass  # las estadísticas nunca deben romper al agente


def registrar() -> bool:
    """Instala el callback global; devuelve False si LangChain no está."""
    global _registrado
    if _registrado:
        return True
    try:
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.tracers.context import register_configure_hook
    except Exception:  # noqa: BLE001 - sin LangChain no hay qué medir
        return False
    from contextvars import ContextVar

    class RegistroLLM(BaseCallbackHandler):
        raise_error = False

        def __init__(self) -> None:
            self._inicios: dict = {}

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self._inicios[run_id] = time.time()

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self._inicios[run_id] = time.time()

        def on_llm_end(self, response, *, run_id, **kwargs):
            fin = time.time()
            inicio = self._inicios.pop(run_id, None)
            for lista in getattr(response, "generations", None) or []:
                for generacion in lista or []:
                    info = _info_generacion(generacion)
                    _escribir({
                        "pid": os.getpid(),
                        "proceso": (
                            "orquestador"
                            if str(os.getpid()) == os.environ.get("DEEPEVAL_PID_PRINCIPAL")
                            else "subproceso_mcp"
                        ),
                        "modelo": info.get("model") or info.get("model_name"),
                        "tokens_entrada": info.get("prompt_eval_count"),
                        "tokens_salida": info.get("eval_count"),
                        "s_total": _segundos(info.get("total_duration")),
                        "s_carga": _segundos(info.get("load_duration")),
                        "s_prompt": _segundos(info.get("prompt_eval_duration")),
                        "s_generacion": _segundos(info.get("eval_duration")),
                        "s_pared": round(fin - inicio, 4) if inicio else None,
                        "fin": fin,
                    })
                    break  # una línea por llamada

        def on_llm_error(self, error, *, run_id, **kwargs):
            inicio = self._inicios.pop(run_id, None)
            _escribir({
                "pid": os.getpid(),
                "error": type(error).__name__,
                "s_pared": round(time.time() - inicio, 4) if inicio else None,
                "fin": time.time(),
            })

    # Con env_var, LangChain crea el handler en cualquier hilo o subproceso
    # donde la variable exista; no depende de que el ContextVar se propague.
    register_configure_hook(
        ContextVar("deepeval_registro_llm", default=None),
        inheritable=True,
        handle_class=RegistroLLM,
        env_var=VARIABLE,
    )
    _registrado = True
    return True
