from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from proyecto.config.models import modelo_de

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "memory.yaml"


class ConfiguracionMemoriaError(RuntimeError):
    """La configuración de memoria no existe o no cumple el contrato."""


def _requerido(data: dict, clave: str):
    if clave not in data:
        raise ConfiguracionMemoriaError(
            f"Falta '{clave}' en {_CONFIG_PATH.name}."
        )
    return data[clave]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalizado = value.strip().lower()
        if normalizado in {"1", "true", "si", "sí", "yes", "on"}:
            return True
        if normalizado in {"0", "false", "no", "off"}:
            return False
    raise ConfiguracionMemoriaError(
        f"'enabled' debe ser booleano en {_CONFIG_PATH.name}."
    )


def _as_int(value: object, clave: str, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError) as error:
        raise ConfiguracionMemoriaError(
            f"'{clave}' debe ser un entero en {_CONFIG_PATH.name}."
        ) from error


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool
    user: str
    max_messages: int
    update_every: int
    max_chars: int
    directory: Path


def config_from_file(path: Path | None = None) -> MemoryConfig:
    config_path = path or _CONFIG_PATH
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfiguracionMemoriaError(
            f"No se pudo leer la configuración de memoria: {config_path}"
        ) from error
    if not isinstance(data, dict):
        raise ConfiguracionMemoriaError(
            f"La configuración de memoria debe ser un mapa: {config_path}"
        )

    user = str(_requerido(data, "user")).strip()
    if not user:
        raise ConfiguracionMemoriaError(
            f"'user' no puede estar vacío en {config_path.name}."
        )
    directory = Path(str(_requerido(data, "directory"))).expanduser()
    if not directory.is_absolute():
        directory = _PROJECT_ROOT / directory

    return MemoryConfig(
        enabled=_as_bool(_requerido(data, "enabled")),
        user=user,
        max_messages=_as_int(_requerido(data, "max_messages"), "max_messages", 2),
        update_every=_as_int(_requerido(data, "update_every"), "update_every", 1),
        max_chars=_as_int(_requerido(data, "max_chars"), "max_chars", 200),
        directory=directory,
    )


class LongTermMemory:
    def __init__(
        self,
        model: str | None = None,
        config: MemoryConfig | None = None,
    ) -> None:
        self.model = model or modelo_de("orchestrator")
        self.config = config or config_from_file()
        self._turns = 0
        self._llm = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def file_path(self) -> Path:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", self.config.user)
        name = name.strip("_")[:64] or "demo"
        return self.config.directory / f"{name}.md"

    def read(self) -> str:
        if not self.enabled:
            return ""
        try:
            return self.file_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return ""

    def save(self, text: str) -> None:
        if not self.enabled:
            return
        text = self._trim(text.strip())
        if not text:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.file_path.with_suffix(".tmp")
        temp_file.write_text(f"{text}\n", encoding="utf-8")
        temp_file.replace(self.file_path)

    def prepare(self, messages: list, query: str) -> list:
        if not self.enabled:
            return list(messages) + [{"role": "user", "content": query}]

        recent = [
            message for message in messages if self._get_role(message) != "system"
        ][-self.config.max_messages :]

        payload: list = []
        summary = self.read()
        if summary:
            payload.append(
                {
                    "role": "system",
                    "content": f"Memoria de largo plazo del usuario:\n{summary}",
                }
            )
        payload.extend(recent)
        payload.append({"role": "user", "content": query})
        return payload

    def update(self, messages: list) -> None:
        if not self.enabled:
            return

        self._turns += 1
        if self._turns % self.config.update_every:
            return

        recent = [
            message
            for message in messages
            if self._get_role(message) in {"user", "human", "assistant", "ai"}
        ][-self.config.max_messages :]
        if not recent:
            return

        conversation = "\n".join(
            f"{self._get_role(message)}: {self._get_text(message)}"
            for message in recent
        )
        prompt = (
            "Actualiza la memoria de largo plazo de este usuario.\n"
            "Guarda solo hechos duraderos, preferencias y decisiones de análisis.\n"
            "ESTRICTAMENTE PROHIBIDO incluir saldos, números de cuenta, CLABEs, "
            "nombres completos o cualquier dato financiero sensible.\n"
            f"Devuelve como máximo {self.config.max_chars} caracteres, "
            "en viñetas, sin explicación adicional.\n\n"
            f"Memoria actual:\n{self.read() or '(vacía)'}\n\n"
            f"Conversación reciente:\n{conversation}\n"
        )

        try:
            response = self._get_llm().invoke(prompt)
            new_summary = self._get_text(response).strip()
            if new_summary:
                self.save(new_summary)
        except Exception as error:
            logger.warning("Could not update long-term memory: %s", error)

    def _get_llm(self):
        if self._llm is None:
            from langchain_ollama import ChatOllama

            self._llm = ChatOllama(model=self.model, temperature=0)
        return self._llm

    def _trim(self, text: str) -> str:
        limit = self.config.max_chars
        if len(text) <= limit:
            return text
        cut = text[:limit]
        if "\n" in cut:
            cut = cut.rsplit("\n", 1)[0]
        return cut.rstrip()

    @staticmethod
    def _get_role(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("role", ""))
        return str(getattr(message, "type", ""))

    @staticmethod
    def _get_text(message: Any) -> str:
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return " ".join(part for part in parts if part)
        return str(content)
