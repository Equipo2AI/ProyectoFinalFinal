"""Control de acceso a los archivos que procesa document_intelligence.

Los archivos subidos desde el chat se guardan en un directorio privado por
identidad. Cuando existe una sesión autenticada, las tools de documentos solo
pueden leer rutas que estén dentro de ese directorio. Así el LLM no puede
convertir en una lectura arbitraria del disco una ruta mencionada en el chat.

Sin token (CLI, pruebas directas o desarrollo local) se conserva el acceso a
rutas locales para no romper los flujos que no pasan por la UI.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

_ENV_RAIZ = "DOCUMENT_UPLOAD_ROOT"
_RAIZ_POR_DEFECTO = Path(__file__).resolve().parents[1] / "data" / "uploads"


def upload_root() -> Path:
    """Devuelve la raíz privada de cargas; se puede cambiar con entorno."""
    configurada = os.getenv(_ENV_RAIZ, "").strip()
    raiz = Path(configurada).expanduser() if configurada else _RAIZ_POR_DEFECTO
    if configurada and not raiz.is_absolute():
        raiz = Path(__file__).resolve().parents[2] / raiz
    raiz.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        raiz.chmod(0o700)
    except OSError:
        pass
    return raiz.resolve()


def _clave_sujeto(subject: str) -> str:
    normalizado = str(subject or "").strip().casefold()
    if not normalizado:
        raise ValueError("Falta la identidad para autorizar el archivo.")
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def subject_upload_root(subject: str) -> Path:
    """Directorio opaco y estable para los archivos de una identidad."""
    raiz = upload_root() / _clave_sujeto(subject)
    raiz.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        raiz.chmod(0o700)
    except OSError:
        pass
    return raiz.resolve()


def new_upload_dir(subject: str) -> Path:
    """Crea un directorio de carga aislado para una sesión de la UI."""
    destino = subject_upload_root(subject) / uuid.uuid4().hex
    destino.mkdir(mode=0o700, parents=False, exist_ok=False)
    return destino.resolve()


def _subject_from_token(token: str | None) -> str | None:
    efectivo = (token if token is not None else os.getenv("PROYECTO_AUTH_TOKEN", "")).strip()
    if not efectivo:
        return None

    from proyecto.security.local_auth import authenticated, principal

    with authenticated(efectivo):
        return str(principal().get("subject") or "").strip() or None


def authorize_file_access(
    path: str | Path,
    *,
    subject: str | None = None,
    token: str | None = None,
    allow_unauthenticated: bool = True,
) -> Path:
    """Valida que ``path`` pueda leerse con la identidad actual.

    Si se recibe ``subject`` se usa directamente. Si no, se intenta resolver
    desde ``token`` o desde ``PROYECTO_AUTH_TOKEN``. Un token inválido siempre
    se rechaza; solo la ausencia total de identidad permite el modo local.
    """
    resuelta = Path(path).expanduser().resolve()
    if subject is not None:
        identidad = str(subject).strip()
        if not identidad:
            raise ValueError("Falta la identidad para autorizar el archivo.")
    else:
        identidad = _subject_from_token(token)

    if not identidad:
        if allow_unauthenticated:
            return resuelta
        raise PermissionError(
            "Acceso denegado: se requiere una identidad autenticada para leer el archivo."
        )

    raiz = subject_upload_root(identidad)
    if not resuelta.is_relative_to(raiz):
        raise PermissionError(
            "Acceso denegado: el archivo no pertenece a las cargas del usuario autenticado."
        )
    return resuelta
