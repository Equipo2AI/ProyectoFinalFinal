"""Caché en disco para metadatos del Data Analyst.

Cada llamada MCP arranca un subproceso nuevo, así que un diccionario en memoria
no sobrevive entre consultas. Este caché guarda solo esquema y catálogo; nunca
filas, resultados ni datos personales. La escritura es atómica para tolerar
subprocesos concurrentes.
"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_VERSION = 1
MAX_ENTRADAS = 500
_RUTA_POR_DEFECTO = (
    Path(__file__).resolve().parents[1] / "data" / "data_analyst_metadata_cache.json"
)


def _normalizar_ruta(valor: str | Path) -> Path:
    ruta = Path(valor).expanduser()
    if not ruta.is_absolute():
        ruta = Path(__file__).resolve().parents[2] / ruta
    return ruta


def _ruta_cache() -> Path | None:
    valor = os.getenv("DATA_ANALYST_METADATA_CACHE", "").strip()
    if valor.lower() in {"0", "off", "false", "no"}:
        return None
    return _normalizar_ruta(valor) if valor else _RUTA_POR_DEFECTO


def _ttl_cache() -> float:
    valor = os.getenv("DATA_ANALYST_METADATA_CACHE_TTL", "3600")
    try:
        return max(0.0, float(valor))
    except (TypeError, ValueError):
        return 3600.0


class MetadataCache:
    """Caché JSON con TTL; si la ruta es ``None`` queda deshabilitado."""

    def __init__(self, path: str | Path | None = None, ttl: float | None = None):
        self.path = _normalizar_ruta(path) if path is not None else _ruta_cache()
        self.ttl = _ttl_cache() if ttl is None else max(0.0, float(ttl))
        self._memoria: dict[str, dict[str, Any]] = {}
        self._cargado = False

    @property
    def habilitado(self) -> bool:
        return self.path is not None

    def _leer(self) -> dict[str, Any]:
        if self.path is None:
            return {"version": CACHE_VERSION, "entries": {}}
        try:
            datos = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {"version": CACHE_VERSION, "entries": {}}
        if not isinstance(datos, dict) or datos.get("version") != CACHE_VERSION:
            return {"version": CACHE_VERSION, "entries": {}}
        entradas = datos.get("entries")
        return {
            "version": CACHE_VERSION,
            "entries": entradas if isinstance(entradas, dict) else {},
        }

    def _cargar(self) -> None:
        if self._cargado:
            return
        self._memoria = self._leer()["entries"]
        self._cargado = True

    def get(self, clave: str) -> Any | None:
        if not self.habilitado:
            return None
        self._cargar()
        entrada = self._memoria.get(clave)
        if not isinstance(entrada, dict):
            return None
        expira = entrada.get("expires")
        if self.ttl and isinstance(expira, (int, float)) and expira < time.time():
            self._memoria.pop(clave, None)
            return None
        return copy.deepcopy(entrada.get("value"))

    def set(self, clave: str, valor: Any) -> None:
        if not self.habilitado:
            return
        self._cargar()
        expira = time.time() + self.ttl if self.ttl else None
        self._memoria[clave] = {
            "expires": expira,
            "value": copy.deepcopy(valor),
        }
        self._guardar()

    def _guardar(self) -> None:
        if self.path is None:
            return
        try:
            ahora = time.time()
            # Se mezcla con lo que haya escrito otro subproceso para no perder
            # entradas válidas; ante una carrera gana la última escritura atómica.
            entradas = self._leer()["entries"]
            entradas.update(self._memoria)
            entradas = {
                clave: entrada
                for clave, entrada in entradas.items()
                if isinstance(entrada, dict)
                and (
                    not self.ttl
                    or not isinstance(entrada.get("expires"), (int, float))
                    or entrada["expires"] >= ahora
                )
            }
            if len(entradas) > MAX_ENTRADAS:
                entradas = dict(
                    sorted(
                        entradas.items(),
                        key=lambda item: item[1].get("expires") or float("inf"),
                        reverse=True,
                    )[:MAX_ENTRADAS]
                )

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporal = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            temporal.write_text(
                json.dumps(
                    {"version": CACHE_VERSION, "entries": entradas},
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
            os.replace(temporal, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except (OSError, TypeError, ValueError):
            # La caché es una optimización: si no se puede escribir, la
            # consulta sigue funcionando sin ella.
            pass

    def clear(self) -> None:
        self._memoria = {}
        self._cargado = True
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass
