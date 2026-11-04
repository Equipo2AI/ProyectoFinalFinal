"""Configuración compartida de la base sintética.

Data Steward y Data Analyst deben apuntar a la misma fuente. La prioridad es:
``DATABASE_URL`` > ``proyecto/config/data_connection.yaml`` > base local
``proyecto/bd/banamex.db``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, event

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = _PROJECT_ROOT / "bd" / "banamex.db"
DEFAULT_DB_NAME = DEFAULT_DB_PATH.name
_CONFIG_PATH = _PROJECT_ROOT / "config" / "data_connection.yaml"

# Tablas que el Data Analyst no debe usar: datos personales y la capa cruda
# que solo se usa para inyectar defectos de calidad. El Data Steward sí puede
# inspeccionar la capa cruda, pero solo devuelve conteos.
TABLAS_EXCLUIDAS = {
    "dim_cliente_persona",
    "raw_clientes",
    "raw_saldos",
    "raw_transacciones",
    "calidad_hallazgos_esperados",
}

COLUMNAS_SENSIBLES = {
    "correo",
    "email",
    "telefono",
    "celular",
    "domicilio",
    "curp",
    "rfc",
}


def _url_desde_config() -> str | None:
    """Lee la ruta SQLite de ``config/data_connection.yaml`` si existe."""
    if not _CONFIG_PATH.is_file():
        return None
    try:
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            return None
        fuente = config.get("data_source") or {}
        if not isinstance(fuente, dict):
            return None
        if fuente.get("type", "sqlite") != "sqlite":
            return None
        ruta = fuente.get("path")
        if not ruta:
            return None
        ruta_db = Path(str(ruta)).expanduser()
        if not ruta_db.is_absolute():
            ruta_db = (_PROJECT_ROOT / ruta_db).resolve()
        return f"sqlite:///{ruta_db.as_posix()}"
    except (OSError, ValueError, yaml.YAMLError):
        return None


def get_database_url() -> str | None:
    """Devuelve DATABASE_URL, la ruta del YAML o la base demo local."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    url = _url_desde_config()
    if url:
        return url
    if DEFAULT_DB_PATH.is_file():
        return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    return None


def _sqlite_solo_lectura(conexion_dbapi, _registro) -> None:
    conexion_dbapi.execute("PRAGMA query_only = ON")


def create_readonly_engine(url_db: str):
    """Crea un engine de solo lectura para SQLite o PostgreSQL."""
    connect_args = {}
    if url_db.startswith("postgresql"):
        connect_args["options"] = "-c default_transaction_read_only=on"

    engine = create_engine(url_db, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_solo_lectura)
    return engine


def objetos_disponibles(inspector, excluir: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Devuelve las tablas y vistas que no estén en la lista de exclusión."""
    excluidas = TABLAS_EXCLUIDAS if excluir is None else excluir
    tablas = [
        tabla
        for tabla in inspector.get_table_names()
        if tabla not in excluidas
    ]
    vistas = [
        vista
        for vista in inspector.get_view_names()
        if vista not in excluidas
    ]
    return tablas, vistas
