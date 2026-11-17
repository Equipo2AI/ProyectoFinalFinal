"""Configuración de pytest para la suite de evaluación con DeepEval.

IMPORTANTE: esta carpeta se llama igual que la librería ``deepeval``. Funciona
porque NO tiene ``__init__.py``; si se agrega uno, ``import deepeval`` cargaría
esta carpeta en lugar de la librería y la suite dejaría de funcionar.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent

# La raíz permite ``import proyecto``; AQUI permite importar los módulos de la
# suite (agente, cargador, metricas) sin convertir la carpeta en paquete.
for ruta in (str(RAIZ), str(AQUI)):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

# Ajustes que deben existir antes de importar deepeval.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")  # sin telemetría externa
# Los modelos locales son lentos: 15 min por caso antes de marcar timeout.
os.environ.setdefault("DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE", "900")

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
except ImportError:  # el agente carga su .env por su cuenta
    pass

from cargador import cargar_casos  # noqa: E402


def pytest_configure(config):
    """Registra cada etiqueta de los YAML como marker (``-m smoke``)."""
    etiquetas = {e for caso in cargar_casos() for e in caso.etiquetas}
    for etiqueta in sorted(etiquetas):
        config.addinivalue_line(
            "markers", f"{etiqueta}: casos etiquetados '{etiqueta}' en deepeval/casos/"
        )
