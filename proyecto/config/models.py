"""Modelos efectivos por agente, leídos de config/models.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

RUTA_MODELOS = Path(__file__).with_name("models.yaml")

MODELOS_POR_DEFECTO = {
    "orchestrator": "mimo-v2.6",
    "data_steward": "qwen2.5:7b-instruct",
    "normatividad_rag": "llama3.1:latest",
    "data_analyst": "mimo-v2.6",
    "document_intelligence": "qwen2.5vl:7b",
    "compliance": "qwen2.5:7b-instruct",
}


@lru_cache(maxsize=1)
def _agentes() -> dict:
    try:
        datos = yaml.safe_load(RUTA_MODELOS.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    agentes = datos.get("agents")
    return agentes if isinstance(agentes, dict) else {}


def modelo_de(agente: str) -> str:
    """Devuelve el modelo configurado para un agente.

    Si el archivo no existe, no es válido o no define el agente, se usa el
    valor por defecto del código para no romper el arranque.
    """
    entrada = _agentes().get(agente)
    if isinstance(entrada, dict):
        valor = entrada.get("model")
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return MODELOS_POR_DEFECTO.get(agente, "llama3.1:latest")


def recargar() -> None:
    """Limpia la caché del archivo; útil para pruebas que editan el YAML."""
    _agentes.cache_clear()
