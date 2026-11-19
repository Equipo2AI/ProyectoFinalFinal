"""Configuración de pytest para la suite de evaluación con DeepEval.

IMPORTANTE: esta carpeta se llama igual que la librería ``deepeval``. Funciona
porque NO tiene ``__init__.py``; si se agrega uno, ``import deepeval`` cargaría
esta carpeta en lugar de la librería y la suite dejaría de funcionar.

Aislamiento de la corrida (se hace aquí, antes de importar el proyecto):

- ``PROYECTO_AUTH_DIR`` apunta a una carpeta temporal: las identidades de
  prueba nunca tocan tu AppData real. ``local_auth`` calcula su carpeta al
  importarse, por eso esto debe ocurrir primero.
- ``DOCUMENT_CONTEXT_DB`` y ``DOCUMENT_UPLOAD_ROOT`` también son temporales.
- Se instala el registro de llamadas LLM (tokens/tiempos de Ollama) en este
  proceso y en los subprocesos MCP.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
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

# ---- aislamiento: siempre después de .env para que no lo sobrescriba ----
_TEMPORAL = Path(tempfile.mkdtemp(prefix="deepeval_kei_"))
os.environ["PROYECTO_AUTH_DIR"] = os.getenv("DEEPEVAL_AUTH_DIR") or str(_TEMPORAL / "auth")
os.environ["DOCUMENT_CONTEXT_DB"] = str(_TEMPORAL / "document_context.db")
os.environ["DOCUMENT_UPLOAD_ROOT"] = str(_TEMPORAL / "uploads")
os.environ["DEEPEVAL_CONTEXTO_TEMPORAL"] = "1"
os.environ.pop("PROYECTO_AUTH_TOKEN", None)  # cada caso abre su propia sesión
# Los casos autenticados deben comprobar que no se usa LangSmith (US-09).
os.environ.setdefault("LANGSMITH_TRACING", "false")
if "proyecto.security.local_auth" in sys.modules:  # pragma: no cover - salvaguarda
    raise RuntimeError(
        "local_auth se importó antes de aislar PROYECTO_AUTH_DIR; "
        "no importes el proyecto desde otro conftest/plugin."
    )


def _borrar_temporal() -> None:
    if not os.getenv("DEEPEVAL_CONSERVAR_TEMPORAL"):
        shutil.rmtree(_TEMPORAL, ignore_errors=True)


atexit.register(_borrar_temporal)

from cargador import cargar_casos, catalogo_historias  # noqa: E402
from rendimiento import CORRIDA, activo, instalar_registro_llm  # noqa: E402

if activo() and os.getenv("DEEPEVAL_LLM_STATS", "1") != "0":
    instalar_registro_llm()

_RESULTADO = {
    "passed": "paso",
    "failed": "fallo",
    "skipped": "omitido",
}


def pytest_configure(config):
    """Registra cada etiqueta de los YAML como marker (``-m smoke``, ``-m us03``)."""
    etiquetas = {e for caso in cargar_casos() for e in caso.etiquetas}
    for etiqueta in sorted(etiquetas):
        config.addinivalue_line(
            "markers", f"{etiqueta}: casos etiquetados '{etiqueta}' en deepeval/casos/"
        )


def pytest_sessionstart(session):
    if activo() and not session.config.option.collectonly:
        CORRIDA.equipo_actual()  # una sola vez, antes del primer caso


def pytest_runtest_logreport(report):
    """Guarda el resultado de cada caso para el resumen de rendimiento."""
    if "test_caso[" not in report.nodeid:
        return
    caso_id = report.nodeid.split("test_caso[", 1)[1].rstrip("]")
    if report.when == "call" or (report.when == "setup" and report.skipped):
        estado = _RESULTADO.get(report.outcome, report.outcome)
        if hasattr(report, "wasxfail"):
            estado = "ya_pasa" if report.passed else "fallo_conocido"
        CORRIDA.caso(caso_id)["resultado"] = estado


def _cobertura() -> dict:
    """Criterio -> casos que lo cubren y su peor resultado."""
    orden = ["fallo", "fallo_conocido", "omitido", "ya_pasa", "paso"]
    casos = cargar_casos()
    matriz: dict[str, dict] = {}
    for historia, datos in catalogo_historias().items():
        deterministas = set(datos.get("pruebas_deterministas") or [])
        for criterio in datos.get("criterios") or {}:
            clave = f"{historia}.{criterio}"
            ids = [c.id for c in casos if clave in c.cubre]
            estados = [
                CORRIDA.casos.get(i, {}).get("resultado") for i in ids
                if CORRIDA.casos.get(i, {}).get("resultado")
            ]
            if estados:
                estado = min(estados, key=lambda e: orden.index(e) if e in orden else 0)
                if len(estados) < len(ids):
                    estado += f" ({len(estados)}/{len(ids)} casos ejecutados)"
            elif ids:
                estado = "no_ejecutado"
            else:
                estado = "sin_caso_agente"
            if criterio in deterministas:
                estado += " · prueba determinista en tests/historias"
            matriz[clave] = {"casos": ids, "estado": estado}
    return matriz


def pytest_sessionfinish(session, exitstatus):
    if not activo():
        return
    carpeta = Path(os.getenv("DEEPEVAL_RESULTS_FOLDER") or AQUI / "resultados")
    if not carpeta.is_absolute():
        carpeta = RAIZ / carpeta
    try:
        rutas = CORRIDA.escribir(carpeta, _cobertura())
    except Exception as error:  # noqa: BLE001 - el resumen no debe ocultar el resultado
        print(f"\n[rendimiento] no se pudo escribir el resumen: {error}", file=sys.stderr)
        return
    if rutas:
        print(f"\n[rendimiento] {rutas[1].relative_to(RAIZ) if rutas[1].is_relative_to(RAIZ) else rutas[1]}")
