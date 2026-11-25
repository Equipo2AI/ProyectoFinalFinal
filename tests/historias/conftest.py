"""Fixtures de las pruebas deterministas por historia de usuario (sin LLM).

Estas pruebas verifican los criterios de aceptación que se pueden comprobar en
los componentes (autorización, guard SQL, herramientas del Data Steward,
gráficas, recuperación local, archivos, contexto documental, trazas) sin
Ollama, Pinecone ni red. Complementan la suite DeepEval de ``deepeval/``.

Requieren la base sintética: ``uv run python proyecto/bd/cargar_bd.py``.
Las identidades se crean en una carpeta temporal; nunca tocan tu AppData.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

CLAVE_LONGITUD = 24


def _ruta_bd() -> Path:
    from sqlalchemy.engine import make_url

    from proyecto.agents.database import get_database_url

    return Path(make_url(get_database_url()).database).resolve()


@pytest.fixture(scope="session")
def base_sintetica() -> Path:
    ruta = _ruta_bd()
    if not ruta.is_file():
        pytest.skip(
            "Falta la base sintética; genera proyecto/bd/banamex.db con "
            "`uv run python proyecto/bd/cargar_bd.py`."
        )
    return ruta


@pytest.fixture(scope="session")
def consultar(base_sintetica):
    """Lectura directa de la base para obtener valores de referencia."""

    def _consultar(sql: str, *parametros):
        uri = base_sintetica.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conexion:
            return conexion.execute(sql, parametros).fetchall()

    return _consultar


@dataclass
class Operador:
    """Identidades aprovisionadas en una carpeta temporal de la sesión."""

    raiz: Path
    claves: dict[str, str] = field(default_factory=dict)

    @contextmanager
    def sesion(self, nombre: str):
        from proyecto.security import local_auth

        token = local_auth.login(nombre, self.claves[nombre])
        try:
            with local_auth.authenticated(token):
                yield token
        finally:
            local_auth.logout(token)

    def registro(self, nombre: str) -> dict:
        from proyecto.security import local_auth

        return dict(local_auth._read()["users"][nombre])


IDENTIDADES = {
    "cliente_1": ("normal", [1], []),
    "cliente_2": ("normal", [2], []),
    "auditor": ("auditor", "todos", ["aggregate", "audit", "governance", "pii:nombre", "pii:rfc"]),
    "analista": ("auditor", "todos", ["aggregate", "audit"]),
    "data_steward": ("auditor", "todos", ["governance", "aggregate"]),
}


@pytest.fixture(scope="session")
def operador(tmp_path_factory, base_sintetica, consultar):
    """Actúa como operador: crea las identidades de prueba (US-01.CA-01)."""
    import secrets

    from proyecto.security import local_auth

    raiz = tmp_path_factory.mktemp("auth")
    parche = pytest.MonkeyPatch()
    # local_auth fija su carpeta al importarse; se redirigen sus globales.
    parche.setattr(local_auth, "ROOT", raiz)
    parche.setattr(local_auth, "STATE", raiz / "identities.json")
    parche.setenv("PROYECTO_AUTH_DIR", str(raiz))
    parche.delenv("PROYECTO_AUTH_TOKEN", raising=False)
    todos = [fila[0] for fila in consultar("SELECT num_cte FROM dim_cliente")]
    resultado = Operador(raiz)
    for nombre, (rol, clientes, permisos) in IDENTIDADES.items():
        clave = secrets.token_urlsafe(CLAVE_LONGITUD)
        local_auth.provision(nombre, clave, rol, todos if clientes == "todos" else clientes, permisos)
        resultado.claves[nombre] = clave
    yield resultado
    parche.undo()


@pytest.fixture
def almacen_documentos(tmp_path):
    from proyecto.memory.document_context import DocumentContextConfig, DocumentContextStore

    return DocumentContextStore(DocumentContextConfig(db_path=tmp_path / "contexto.db"))


@pytest.fixture
def carga_de(operador, tmp_path, monkeypatch):
    """Crea un archivo dentro de la carpeta de cargas de una identidad."""
    from proyecto.security import file_access

    monkeypatch.setenv("DOCUMENT_UPLOAD_ROOT", str(tmp_path / "uploads"))

    def _crear(subject: str, nombre: str, contenido: bytes) -> Path:
        destino = file_access.new_upload_dir(subject) / nombre
        destino.write_bytes(contenido)
        return destino

    return _crear
