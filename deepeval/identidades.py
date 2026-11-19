"""Aprovisiona y abre sesión con las identidades de prueba (US-01).

El servicio solo concede permisos a una sesión real de ``local_auth``; el
``perfil`` que viaja con la consulta no otorga nada. Por eso la suite actúa
como operador: crea identidades en una carpeta privada temporal, inicia sesión
por caso y entrega el token al servicio por ``PROYECTO_AUTH_TOKEN``.

IMPORTANTE: ``PROYECTO_AUTH_DIR`` debe definirse ANTES de importar
``proyecto.security.local_auth`` (calcula su carpeta al importarse). Lo hace
``conftest.py`` al arrancar.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
ARCHIVO_IDENTIDADES = AQUI / "identidades.yaml"
SIN_SESION = "sin_sesion"


class IdentidadDesconocida(KeyError):
    pass


@lru_cache(maxsize=1)
def definiciones() -> dict[str, dict]:
    datos = yaml.safe_load(ARCHIVO_IDENTIDADES.read_text(encoding="utf-8")) or {}
    identidades = datos.get("identidades") or {}
    if not isinstance(identidades, dict) or not identidades:
        raise ValueError(f"{ARCHIVO_IDENTIDADES.name}: falta el mapa 'identidades'")
    if SIN_SESION in identidades:
        raise ValueError(f"'{SIN_SESION}' es un nombre reservado")
    return identidades


def _todos_los_clientes(ruta_bd: str) -> list[int]:
    uri = Path(ruta_bd).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conexion:
        return [fila[0] for fila in conexion.execute("SELECT num_cte FROM dim_cliente")]


class Identidades:
    """Registro de identidades de una corrida: una clave aleatoria cada vez."""

    def __init__(self) -> None:
        self._claves: dict[str, str] = {}
        self._aprovisionadas = False

    def aprovisionar(self) -> None:
        if self._aprovisionadas:
            return
        from proyecto.security import local_auth

        ruta_bd, _digest = local_auth.db_binding()
        existentes = local_auth._read()["users"]
        for nombre, definicion in definiciones().items():
            clientes = definicion.get("clientes")
            if clientes == "todos":
                clientes = _todos_los_clientes(ruta_bd)
            clave = secrets.token_urlsafe(24)  # >= 16 caracteres; no se imprime
            if nombre in existentes:
                raise RuntimeError(
                    f"La identidad de prueba {nombre!r} ya existe en "
                    f"{local_auth.ROOT}. La suite debe usar una carpeta temporal."
                )
            local_auth.provision(
                nombre,
                clave,
                str(definicion["rol"]),
                [int(c) for c in clientes],
                [str(p) for p in definicion.get("permisos") or []],
            )
            self._claves[nombre] = clave
        self._aprovisionadas = True

    def huella(self, nombre: str) -> str | None:
        """Hash del registro de la identidad (rol, alcance, permisos)."""
        if nombre in (None, SIN_SESION):
            return None
        from proyecto.security import local_auth

        usuario = local_auth._read()["users"].get(nombre)
        if usuario is None:
            return None
        relevante = {k: usuario[k] for k in ("role", "customers", "permissions")}
        return hashlib.sha256(json.dumps(relevante, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def sesion(self, nombre: str | None):
        """Abre sesión y deja el token en el entorno; lo revoca al salir."""
        from proyecto.security import local_auth

        anterior = os.environ.get("PROYECTO_AUTH_TOKEN")
        _olvidar_token_streamlit()
        token = ""
        try:
            if nombre and nombre != SIN_SESION:
                if nombre not in definiciones():
                    raise IdentidadDesconocida(
                        f"Identidad {nombre!r} no está en {ARCHIVO_IDENTIDADES.name}"
                    )
                self.aprovisionar()
                token = local_auth.login(nombre, self._claves[nombre])
            if token:
                os.environ["PROYECTO_AUTH_TOKEN"] = token
            else:
                os.environ.pop("PROYECTO_AUTH_TOKEN", None)
            yield nombre
        finally:
            if token:
                try:
                    local_auth.logout(token)
                except Exception:  # noqa: BLE001 - la carpeta es temporal
                    pass
            if anterior is None:
                os.environ.pop("PROYECTO_AUTH_TOKEN", None)
            else:
                os.environ["PROYECTO_AUTH_TOKEN"] = anterior
            _olvidar_token_streamlit()


def _olvidar_token_streamlit() -> None:
    """El servicio prioriza el token de Streamlit; fuera de la UI no debe haber."""
    try:
        import streamlit as st

        st.session_state.pop("auth_token", None)
    except Exception:  # noqa: BLE001 - sin Streamlit no hay nada que limpiar
        pass


REGISTRO = Identidades()
