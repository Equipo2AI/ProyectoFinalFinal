"""Configuración mínima de pytest para el paquete ``proyecto``."""

from __future__ import annotations

import sys
import os
from pathlib import Path

# Las pruebas usan datos sinteticos; no exportar entradas ni trazas a terceros.
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


import secrets
import sqlite3
from contextlib import ExitStack
import pytest


@pytest.fixture
def sesion_local(tmp_path, monkeypatch):
    """Identidades reales sobre SQLite sintetica; nunca permisos por un perfil suelto."""
    from proyecto.security import local_auth as auth
    scope_contexts = ExitStack()
    root = tmp_path / 'auth'
    monkeypatch.setattr(auth, 'ROOT', root)
    monkeypatch.setattr(auth, 'STATE', root / 'identities.json')
    monkeypatch.setenv('PROYECTO_AUTH_DIR', str(root))
    def activar(db, rol='normal', permisos=None):
        db = Path(db).resolve()
        monkeypatch.setenv('DATABASE_URL', f'sqlite:///{db.as_posix()}')
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as c:
            customers = [r[0] for r in c.execute('SELECT num_cte FROM dim_cliente ORDER BY num_cte')]
        scope = customers if rol == 'auditor' else customers[:1]
        password = secrets.token_urlsafe(24)
        username = 'fixture_' + secrets.token_hex(6)
        auth.provision(username, password, rol, scope,
                       permisos if permisos is not None else (['audit','aggregate','governance','pii:nombre','pii:rfc'] if rol == 'auditor' else []))
        token = auth.login(username, password)
        scope_contexts.enter_context(auth.authenticated(token))
        return token
    yield activar
    scope_contexts.close()


@pytest.fixture
def identidad_prueba(tmp_path, sesion_local):
    """Sesion normal para pruebas de contrato que antes corrian anonimas."""
    db = tmp_path / 'identidad.db'
    with sqlite3.connect(db) as c:
        c.executescript((RAIZ / 'proyecto/bd/crear_bd.sql').read_text(encoding='utf-8'))
    return sesion_local(db)
