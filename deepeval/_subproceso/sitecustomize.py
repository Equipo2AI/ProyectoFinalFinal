"""Se ejecuta al arrancar cada subproceso MCP durante la suite DeepEval.

La suite agrega esta carpeta a ``PYTHONPATH`` solo mientras corre. Si
``DEEPEVAL_LLM_STATS_FILE`` no está definida, no hace nada. Después delega en
cualquier otro ``sitecustomize`` que existiera para no alterar el entorno.
"""

import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))

if os.environ.get("DEEPEVAL_LLM_STATS_FILE"):
    try:
        if _AQUI not in sys.path:
            sys.path.insert(0, _AQUI)
        import registro_llm

        registro_llm.registrar()
    except Exception:  # noqa: BLE001 - nunca romper el subproceso
        pass

try:  # encadenar el sitecustomize original, si hay uno más adelante en sys.path
    import importlib.machinery
    import importlib.util

    _otras = [p for p in sys.path if os.path.abspath(p or ".") != _AQUI]
    _spec = importlib.machinery.PathFinder.find_spec("sitecustomize", _otras)
    if _spec is not None and _spec.origin and os.path.abspath(_spec.origin) != os.path.abspath(__file__):
        _modulo = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_modulo)
except Exception:  # noqa: BLE001
    pass
