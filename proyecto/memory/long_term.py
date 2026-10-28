from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).resolve().parents[1] / "data" / "memory"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = False
    user: str = "demo"
    max_messages: int = 8
    update_every: int = 4
    max_chars: int = 1200
    directory: Path = field(default_factory=lambda: _MEMORY_DIR)


def config_from_env() -> MemoryConfig:
    return MemoryConfig(
        enabled=_env_bool("LONG_TERM_MEMORY_ENABLED"),
        user=os.getenv("MEMORY_USER", "demo").strip() or "demo",
        max_messages=_env_int("MEMORY_MAX_MESSAGES", 8, 2),
        update_every=_env_int("MEMORY_UPDATE_EVERY", 4, 1),
        max_chars=_env_int("MEMORY_MAX_CHARS", 1200, 200),
        directory=Path(os.getenv("MEMORY_DIR", str(_MEMORY_DIR))).expanduser(),
    )


class LongTermMemory:
    def __init__(
        self,
        model: str = "gemma4:e4b",
        config: MemoryConfig | None = None,
    ) -> None:
        self.model = model
        self.config = config or config_from_env()
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
