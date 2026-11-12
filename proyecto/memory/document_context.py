"""Contexto documental privado por usuario.

Los Markdown y OCR de documentos se guardan en un SQLite local, no en Pinecone
ni en el índice normativo compartido. El texto completo se conserva en disco y
solo se envía al modelo un contexto acotado: el documento completo si cabe, o
un resumen extractivo más las secciones relevantes para la consulta.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _PROJECT_ROOT / "data" / "document_context.db"
_RE_ENCABEZADO = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_RE_TOKEN = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = {
    "para", "como", "cual", "cuales", "que", "los", "las", "del", "por",
    "con", "una", "uno", "unos", "unas", "sobre", "este", "esta", "esto",
    "estos", "estas", "documento", "documentos", "archivo", "archivos",
    "dice", "dime", "donde", "cuando", "segun", "relaciona", "relacionar",
    "explica", "explicar", "resume", "resumen", "analiza", "analizar",
    "favor", "puedes", "podrias", "quiero", "necesito", "ayuda", "ayudame",
    "seccion", "secciones", "capitulo", "capitulos",
}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", (texto or "").casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _recortar(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    corte = texto[: max(1, limite)]
    for separador in ("\n\n", "\n", ". ", " "):
        posicion = corte.rfind(separador)
        if posicion >= max(1, int(limite * 0.55)):
            return corte[:posicion].rstrip() + "…"
    return corte.rstrip() + "…"


def _tokens(consulta: str) -> set[str]:
    texto = _normalizar(consulta)
    tokens = {
        token
        for token in _RE_TOKEN.findall(texto)
        if token not in _STOPWORDS and len(token) >= 3
    }
    # Los números de cláusula, sección o página son señales fuertes.
    tokens.update(re.findall(r"\b\d+\b", texto))
    return tokens


def _partir_texto(texto: str, limite: int = 1800) -> list[str]:
    """Parte un bloque largo sin perder párrafos ni cortar a mitad de palabra."""
    texto = (texto or "").strip()
    if not texto:
        return []
    if len(texto) <= limite:
        return [texto]

    partes: list[str] = []
    actual: list[str] = []
    actual_len = 0
    for parrafo in re.split(r"\n\s*\n", texto):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        if len(parrafo) > limite:
            if actual:
                partes.append("\n\n".join(actual).strip())
                actual, actual_len = [], 0
            inicio = 0
            while inicio < len(parrafo):
                partes.append(parrafo[inicio : inicio + limite].strip())
                inicio += limite
            continue
        if actual and actual_len + len(parrafo) + 2 > limite:
            partes.append("\n\n".join(actual).strip())
            actual, actual_len = [], 0
        actual.append(parrafo)
        actual_len += len(parrafo) + 2
    if actual:
        partes.append("\n\n".join(actual).strip())
    return [parte for parte in partes if parte]


def _secciones_markdown(texto: str) -> list[dict[str, Any]]:
    """Índice determinista de secciones; no usa embeddings ni LLM."""
    secciones: list[dict[str, Any]] = []
    titulo = "Contenido"
    buffer: list[str] = []

    def cerrar() -> None:
        nonlocal buffer
        contenido = "\n".join(buffer).strip()
        if contenido:
            for parte in _partir_texto(contenido):
                secciones.append(
                    {
                        "titulo": titulo,
                        "texto": parte,
                        "orden": len(secciones),
                    }
                )
        buffer = []

    for linea in (texto or "").splitlines():
        coincidencia = _RE_ENCABEZADO.match(linea)
        if coincidencia:
            cerrar()
            titulo = coincidencia.group(1).strip() or "Sección"
        else:
            buffer.append(linea)
    cerrar()

    if not secciones and (texto or "").strip():
        for parte in _partir_texto(texto):
            secciones.append(
                {"titulo": "Contenido", "texto": parte, "orden": len(secciones)}
            )
    return secciones


def _resumen_extractivo(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    if not texto:
        return ""
    if len(texto) <= limite:
        return texto

    encabezados = [
        coincidencia.group(1).strip()
        for coincidencia in (
            _RE_ENCABEZADO.match(linea)
            for linea in texto.splitlines()
        )
        if coincidencia
    ]
    cuerpo = "\n".join(
        linea for linea in texto.splitlines()
        if not _RE_ENCABEZADO.match(linea)
    )
    cuerpo = re.sub(r"\s+", " ", cuerpo).strip()

    partes: list[str] = []
    if encabezados:
        indice = " · ".join(dict.fromkeys(encabezados[:12]))
        partes.append(f"Secciones: {indice}")
    restante = max(200, limite - sum(len(parte) + 1 for parte in partes))
    if cuerpo:
        partes.append(_recortar(cuerpo, restante))
    return _recortar("\n".join(partes), limite)


def _seleccionar_secciones(
    secciones: list[dict[str, Any]],
    consulta: str,
    limite: int,
) -> str:
    if not secciones or limite <= 0:
        return ""

    tokens = _tokens(consulta)

    def puntaje(seccion: dict[str, Any]) -> int:
        if not tokens:
            return 0
        contenido = _normalizar(
            f"{seccion.get('titulo', '')} {seccion.get('texto', '')}"
        )
        return sum(1 for token in tokens if token in contenido)

    candidatas = sorted(
        enumerate(secciones),
        key=lambda par: (-puntaje(par[1]), int(par[1].get("orden", par[0]))),
    )

    elegidas: list[tuple[int, dict[str, Any], str]] = []
    restante = limite
    for indice, seccion in candidatas:
        titulo = str(seccion.get("titulo") or "Sección")
        cuerpo = str(seccion.get("texto") or "").strip()
        if not cuerpo:
            continue
        bloque = f"[Sección: {titulo}]\n{cuerpo}"
        if len(bloque) > restante:
            if restante >= 450:
                bloque = _recortar(bloque, restante)
            else:
                continue
        elegidas.append((indice, seccion, bloque))
        restante -= len(bloque) + 2
        if restante <= 200:
            break

    elegidas.sort(key=lambda item: int(item[1].get("orden", item[0])))
    return "\n\n".join(bloque for _, _, bloque in elegidas)


@dataclass(frozen=True)
class DocumentContextConfig:
    """Límites locales del contexto documental privado."""

    db_path: Path = field(default_factory=lambda: _DEFAULT_DB)
    ttl_days: int = 7
    max_docs: int = 20
    max_chars: int = 10_000
    summary_chars: int = 1_200


def config_from_env() -> DocumentContextConfig:
    ruta = os.getenv("DOCUMENT_CONTEXT_DB", "").strip()
    db_path = Path(ruta).expanduser() if ruta else _DEFAULT_DB
    if not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    return DocumentContextConfig(
        db_path=db_path,
        ttl_days=_env_int("DOCUMENT_CONTEXT_TTL_DAYS", 7, 1),
        max_docs=_env_int("DOCUMENT_CONTEXT_MAX_DOCS", 20, 1),
        max_chars=_env_int("DOCUMENT_CONTEXT_MAX_CHARS", 10_000, 1_500),
        summary_chars=_env_int("DOCUMENT_CONTEXT_SUMMARY_CHARS", 1_200, 300),
    )


class DocumentContextStore:
    """Almacén local y aislado por usuario.

    La clave de privacidad es el usuario autenticado (``subject``). Ninguna
    consulta de este módulo toca Pinecone o el índice normativo compartido.
    """

    def __init__(self, config: DocumentContextConfig | None = None) -> None:
        self.config = config or config_from_env()
        self.db_path = Path(self.config.db_path).expanduser()
        self._init_db()
        self.purge_expired()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conexion = sqlite3.connect(self.db_path, timeout=10)
        conexion.row_factory = sqlite3.Row
        try:
            conexion.execute("PRAGMA foreign_keys = ON")
            yield conexion
            conexion.commit()
        except Exception:
            conexion.rollback()
            raise
        finally:
            conexion.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as conexion:
            conexion.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    nombre TEXT NOT NULL,
                    operacion TEXT NOT NULL,
                    ruta_original TEXT,
                    sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    markdown TEXT NOT NULL,
                    resumen TEXT NOT NULL,
                    secciones TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_contenido
                    ON documents(user_id, sha256, operacion);
                CREATE INDEX IF NOT EXISTS idx_documents_usuario
                    ON documents(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS active_documents (
                    user_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
        try:
            self.db_path.parent.chmod(0o700)
            self.db_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _usuario(user_id: str | None) -> str:
        return str(user_id or "local").strip() or "local"

    def save(
        self,
        user_id: str,
        nombre: str,
        operacion: str,
        texto: str,
        ruta_original: str | None = None,
    ) -> str:
        """Guarda o actualiza un documento del usuario y lo deja como activo."""
        usuario = self._usuario(user_id)
        texto = (texto or "").strip()
        if not texto:
            raise ValueError("El documento no tiene texto para guardar.")

        nombre = (nombre or "documento").strip() or "documento"
        operacion = (operacion or "documento").strip() or "documento"
        ruta = str(ruta_original or "").strip() or None
        sha256 = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        resumen = _resumen_extractivo(texto, self.config.summary_chars)
        secciones = json.dumps(
            _secciones_markdown(texto),
            ensure_ascii=False,
        )
        ahora = time.time()

        with self._connect() as conexion:
            existente = conexion.execute(
                """
                SELECT doc_id FROM documents
                WHERE user_id = ? AND sha256 = ? AND operacion = ?
                """,
                (usuario, sha256, operacion),
            ).fetchone()
            if existente:
                doc_id = str(existente["doc_id"])
                conexion.execute(
                    """
                    UPDATE documents
                    SET nombre = ?, ruta_original = ?, updated_at = ?,
                        markdown = ?, resumen = ?, secciones = ?
                    WHERE user_id = ? AND doc_id = ?
                    """,
                    (
                        nombre,
                        ruta,
                        ahora,
                        texto,
                        resumen,
                        secciones,
                        usuario,
                        doc_id,
                    ),
                )
            else:
                doc_id = uuid.uuid4().hex
                conexion.execute(
                    """
                    INSERT INTO documents (
                        doc_id, user_id, nombre, operacion, ruta_original,
                        sha256, created_at, updated_at, markdown, resumen,
                        secciones
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        usuario,
                        nombre,
                        operacion,
                        ruta,
                        sha256,
                        ahora,
                        ahora,
                        texto,
                        resumen,
                        secciones,
                    ),
                )

            conexion.execute(
                """
                INSERT INTO active_documents (user_id, doc_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    doc_id = excluded.doc_id,
                    updated_at = excluded.updated_at
                """,
                (usuario, doc_id, ahora),
            )

        self._limitar_documentos(usuario)
        return doc_id

    def list(self, user_id: str) -> list[dict[str, Any]]:
        usuario = self._usuario(user_id)
        self.purge_expired(usuario)
        with self._connect() as conexion:
            filas = conexion.execute(
                """
                SELECT doc_id, nombre, operacion, ruta_original, created_at,
                       updated_at, length(markdown) AS chars, resumen
                FROM documents
                WHERE user_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (usuario,),
            ).fetchall()
        return [dict(fila) for fila in filas]

    def get(self, user_id: str, doc_id: str) -> dict[str, Any] | None:
        usuario = self._usuario(user_id)
        with self._connect() as conexion:
            fila = conexion.execute(
                """
                SELECT doc_id, nombre, operacion, ruta_original, created_at,
                       updated_at, markdown, resumen, secciones
                FROM documents
                WHERE user_id = ? AND doc_id = ?
                """,
                (usuario, doc_id),
            ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        datos["chars"] = len(datos.get("markdown") or "")
        try:
            datos["secciones"] = json.loads(datos.get("secciones") or "[]")
        except (TypeError, ValueError):
            datos["secciones"] = []
        return datos

    def get_active(self, user_id: str) -> str | None:
        usuario = self._usuario(user_id)
        with self._connect() as conexion:
            fila = conexion.execute(
                "SELECT doc_id FROM active_documents WHERE user_id = ?",
                (usuario,),
            ).fetchone()
            if fila:
                existe = conexion.execute(
                    "SELECT 1 FROM documents WHERE user_id = ? AND doc_id = ?",
                    (usuario, fila["doc_id"]),
                ).fetchone()
                if existe:
                    return str(fila["doc_id"])
            reciente = conexion.execute(
                """
                SELECT doc_id FROM documents
                WHERE user_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (usuario,),
            ).fetchone()
        if not reciente:
            return None
        return str(reciente["doc_id"])

    def set_active(self, user_id: str, doc_id: str) -> None:
        usuario = self._usuario(user_id)
        ahora = time.time()
        with self._connect() as conexion:
            existe = conexion.execute(
                "SELECT 1 FROM documents WHERE user_id = ? AND doc_id = ?",
                (usuario, doc_id),
            ).fetchone()
            if not existe:
                return
            # El TTL se mide desde el último uso, no desde la carga inicial.
            conexion.execute(
                "UPDATE documents SET updated_at = ? WHERE user_id = ? AND doc_id = ?",
                (ahora, usuario, doc_id),
            )
            conexion.execute(
                """
                INSERT INTO active_documents (user_id, doc_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    doc_id = excluded.doc_id,
                    updated_at = excluded.updated_at
                """,
                (usuario, doc_id, ahora),
            )

    def clear_active(self, user_id: str) -> None:
        with self._connect() as conexion:
            conexion.execute(
                "DELETE FROM active_documents WHERE user_id = ?",
                (self._usuario(user_id),),
            )

    def delete(self, user_id: str, doc_id: str) -> None:
        usuario = self._usuario(user_id)
        with self._connect() as conexion:
            conexion.execute(
                "DELETE FROM active_documents WHERE user_id = ? AND doc_id = ?",
                (usuario, doc_id),
            )
            conexion.execute(
                "DELETE FROM documents WHERE user_id = ? AND doc_id = ?",
                (usuario, doc_id),
            )

    def purge_expired(self, user_id: str | None = None) -> int:
        """Elimina documentos fuera del TTL; devuelve cuántos borró."""
        corte = time.time() - (self.config.ttl_days * 86_400)
        with self._connect() as conexion:
            if user_id:
                cursor = conexion.execute(
                    "DELETE FROM documents WHERE user_id = ? AND updated_at < ?",
                    (self._usuario(user_id), corte),
                )
                conexion.execute(
                    """
                    DELETE FROM active_documents
                    WHERE user_id = ? AND doc_id NOT IN (
                        SELECT doc_id FROM documents WHERE user_id = ?
                    )
                    """,
                    (self._usuario(user_id), self._usuario(user_id)),
                )
            else:
                cursor = conexion.execute(
                    "DELETE FROM documents WHERE updated_at < ?",
                    (corte,),
                )
                conexion.execute(
                    """
                    DELETE FROM active_documents
                    WHERE doc_id NOT IN (SELECT doc_id FROM documents)
                    """
                )
        return int(cursor.rowcount or 0)

    def _limitar_documentos(self, user_id: str) -> None:
        with self._connect() as conexion:
            sobrantes = conexion.execute(
                """
                SELECT doc_id FROM documents
                WHERE user_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT -1 OFFSET ?
                """,
                (user_id, self.config.max_docs),
            ).fetchall()
            ids = [str(fila["doc_id"]) for fila in sobrantes]
            if not ids:
                return
            marcadores = ",".join("?" for _ in ids)
            conexion.execute(
                f"DELETE FROM documents WHERE user_id = ? AND doc_id IN ({marcadores})",
                (user_id, *ids),
            )
            conexion.execute(
                "DELETE FROM active_documents WHERE user_id = ? AND doc_id IN "
                f"({marcadores})",
                (user_id, *ids),
            )

    def _documento_para_consulta(
        self,
        user_id: str,
        consulta: str,
        documentos: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not documentos:
            return None
        activo = self.get_active(user_id) or str(documentos[0]["doc_id"])
        consulta_normal = _normalizar(consulta)
        if consulta_normal:
            for documento in documentos:
                nombre = _normalizar(Path(str(documento.get("nombre") or "")).stem)
                if len(nombre) >= 4 and nombre in consulta_normal:
                    activo = str(documento["doc_id"])
                    self.set_active(user_id, activo)
                    break
        return self.get(user_id, activo) or self.get(
            user_id, str(documentos[0]["doc_id"])
        )

    def build_context(
        self,
        user_id: str,
        consulta: str = "",
        max_chars: int | None = None,
    ) -> str:
        """Construye el contexto privado que verá el orquestador."""
        usuario = self._usuario(user_id)
        self.purge_expired(usuario)
        documentos = self.list(usuario)
        documento = self._documento_para_consulta(usuario, consulta, documentos)
        if not documento:
            return ""
        self.set_active(usuario, str(documento["doc_id"]))

        limite = max(1_500, int(max_chars or self.config.max_chars))
        encabezado = (
            f"Documento activo: {documento.get('nombre')} "
            f"({documento.get('operacion')})\n"
        )
        if documento.get("ruta_original"):
            encabezado += f"Ruta original: {documento['ruta_original']}\n"
        encabezado += f"Caracteres del documento: {documento.get('chars', 0)}\n"

        texto = str(documento.get("markdown") or "")
        disponible = max(600, limite - len(encabezado) - 250)
        if len(texto) <= disponible:
            cuerpo = texto
            nota = ""
        else:
            resumen = str(documento.get("resumen") or "")
            if not resumen:
                resumen = _resumen_extractivo(texto, self.config.summary_chars)
            secciones = documento.get("secciones") or []
            seleccion = _seleccionar_secciones(
                secciones,
                consulta,
                max(450, disponible - len(resumen) - 250),
            )
            cuerpo = (
                "Resumen del documento:\n"
                f"{resumen}\n\n"
                "Secciones relevantes:\n"
                f"{seleccion}"
            )
            nota = (
                "\n[El documento completo está guardado de forma privada; "
                "se enviaron un resumen y las secciones más relevantes para "
                "ajustar el contexto del modelo.]"
            )

        otros = [
            item for item in documentos
            if str(item.get("doc_id")) != str(documento.get("doc_id"))
        ]
        extra = ""
        if otros:
            nombres = ", ".join(
                f"{item.get('nombre')} ({item.get('operacion')})"
                for item in otros[:10]
            )
            extra = (
                "\n\nOtros documentos disponibles del mismo usuario "
                f"(no incluidos completos): {nombres}"
            )

        contexto = f"{encabezado}{cuerpo}{nota}{extra}".strip()
        return _recortar(contexto, limite)
