"""Modelos efectivos por agente, leídos de config/models.yaml.

``models.yaml`` es la única fuente de nombres de modelo. El código no repite
esos nombres: si falta una entrada, el arranque falla con un mensaje claro en
lugar de usar un valor distinto al configurado.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

RUTA_MODELOS = Path(__file__).with_name("models.yaml")


class ConfiguracionModelosError(RuntimeError):
    """La configuración de modelos no existe o no cumple el contrato."""


@lru_cache(maxsize=1)
def _datos() -> dict:
    try:
        texto = RUTA_MODELOS.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfiguracionModelosError(
            f"No se pudo leer la configuración de modelos: {RUTA_MODELOS}"
        ) from error

    try:
        datos = yaml.safe_load(texto) or {}
    except yaml.YAMLError as error:
        raise ConfiguracionModelosError(
            f"La configuración de modelos no es YAML válido: {RUTA_MODELOS}"
        ) from error

    if not isinstance(datos, dict):
        raise ConfiguracionModelosError(
            f"La configuración de modelos debe ser un mapa: {RUTA_MODELOS}"
        )
    return datos


def _modelo(entrada: object, etiqueta: str) -> str:
    if not isinstance(entrada, dict):
        raise ConfiguracionModelosError(
            f"Falta la entrada '{etiqueta}' en {RUTA_MODELOS.name}."
        )
    modelo = entrada.get("model")
    if not isinstance(modelo, str) or not modelo.strip():
        raise ConfiguracionModelosError(
            f"Falta el campo 'model' de '{etiqueta}' en {RUTA_MODELOS.name}."
        )
    return modelo.strip()


def modelo_de(agente: str) -> str:
    """Devuelve el modelo configurado para un agente."""
    agentes = _datos().get("agents")
    if not isinstance(agentes, dict):
        raise ConfiguracionModelosError(
            f"Falta el mapa 'agents' en {RUTA_MODELOS.name}."
        )
    return _modelo(agentes.get(agente), f"agents.{agente}")


def modelo_pdf() -> str:
    """Modelo de OCR de PDF; ``DOCUMENT_PDF_MODEL`` tiene prioridad."""
    override = os.getenv("DOCUMENT_PDF_MODEL", "").strip()
    if override:
        return override
    return _modelo(_datos().get("document_pdf"), "document_pdf")


def recargar() -> None:
    """Limpia la caché del archivo; útil para pruebas que editan el YAML."""
    _datos.cache_clear()


def opciones_ollama(modelo: str) -> dict:
    """Mitigacion reversible de carga de MiMo en Windows, no cambio de modelo.

    Solicitar mmap evita el --load-mode none predeterminado de Windows/CUDA.
    No demuestra reparacion del runtime. PROYECTO_OLLAMA_MMAP=0 la desactiva.
    """
    import sys

    if (
        sys.platform == "win32"
        and modelo.split(":")[0] == "mimo-v2.6"
        and os.environ.get("PROYECTO_OLLAMA_MMAP", "1") != "0"
    ):
        return {"use_mmap": True}
    return {}
