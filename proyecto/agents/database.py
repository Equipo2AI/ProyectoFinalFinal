"""Configuración compartida de la base sintética.

Data Steward y Data Analyst deben apuntar a la misma fuente. La prioridad es:
``DATABASE_URL`` > ``proyecto/config/data_connection.yaml``. No hay una ruta
hardcodeada de respaldo: el YAML es la fuente de la base local.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "data_connection.yaml"


class ConfiguracionDatosError(RuntimeError):
    """La conexión a datos no existe o no cumple el contrato."""


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


def _url_desde_config() -> str:
    """Lee la ruta SQLite de ``config/data_connection.yaml``."""
    if not _CONFIG_PATH.is_file():
        raise ConfiguracionDatosError(
            f"No se encontró la configuración de datos: {_CONFIG_PATH}"
        )
    try:
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfiguracionDatosError(
            f"No se pudo leer la configuración de datos: {_CONFIG_PATH}"
        ) from error

    if not isinstance(config, dict):
        raise ConfiguracionDatosError(
            f"La configuración de datos debe ser un mapa: {_CONFIG_PATH}"
        )
    fuente = config.get("data_source")
    if not isinstance(fuente, dict):
        raise ConfiguracionDatosError(
            f"Falta 'data_source' en {_CONFIG_PATH.name}."
        )
    if fuente.get("type", "sqlite") != "sqlite":
        raise ConfiguracionDatosError(
            f"El tipo de datos configurado no es SQLite: {fuente.get('type')!r}."
        )
    ruta = fuente.get("path")
    if not isinstance(ruta, str) or not ruta.strip():
        raise ConfiguracionDatosError(
            f"Falta 'data_source.path' en {_CONFIG_PATH.name}."
        )
    ruta_db = Path(ruta).expanduser()
    if not ruta_db.is_absolute():
        ruta_db = (_PROJECT_ROOT / ruta_db).resolve()
    return f"sqlite:///{ruta_db.as_posix()}"


def get_database_url() -> str:
    """Devuelve DATABASE_URL o la ruta configurada en el YAML."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return _url_desde_config()


def nombre_base_datos() -> str:
    """Nombre del archivo SQLite configurado, para mostrarlo en las trazas."""
    try:
        database = make_url(get_database_url()).database
    except Exception:  # noqa: BLE001 - solo es una etiqueta de salida
        return ""
    return Path(database).name if database else ""


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
