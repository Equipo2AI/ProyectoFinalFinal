"""Adaptador entre la suite y el agente.

ESTE ES EL ÚNICO ARCHIVO QUE CONOCE AL PROYECTO. Si cambia la forma de invocar
al agente (otro módulo, otra firma, otra estructura de respuesta), se ajusta
aquí y el resto de la suite, incluidos los YAML, sigue igual.

Usa los mismos contratos que la interfaz Streamlit:

- ``proyecto.servicio.preguntar_orquestador(consulta) -> Respuesta``
- ``proyecto.servicio.procesar_imagen_auto(ruta, consulta, nombre)``
- ``proyecto.servicio.procesar_documento(ruta, consulta, nombre)``
- ``proyecto.servicio.reiniciar_orquestador()`` (botón "Limpiar")

Cada caso corre con una sesión real de ``local_auth`` (ver ``identidades.py``)
y se mide con ``rendimiento.medir``.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from cargador import Caso, Turno
from fixtures import preparar_adjunto
from identidades import REGISTRO
from rendimiento import medir

# Tope por fragmento de evidencia que se envía al juez; las salidas JSON del
# Data Steward pueden ser muy largas para un modelo local.
MAX_CARACTERES_CONTEXTO = 4000
_EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


@dataclass
class ResultadoAgente:
    respuesta: str
    agentes: list[str] = field(default_factory=list)
    herramientas: list[str] = field(default_factory=list)
    contexto: list[str] = field(default_factory=list)  # salidas de herramientas
    trazas: list[dict] = field(default_factory=list)
    fuentes: list[str] = field(default_factory=list)
    sql: str | None = None
    datos: list[dict] | None = None
    tiene_grafica: bool = False
    errores: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    duracion: float | None = None  # segundos del turno evaluado (reloj de pared)
    bd_cambio: bool | None = None
    identidad_cambio: bool | None = None
    rendimiento: dict = field(default_factory=dict)


# ---------------------------------------------------------------- servicio


def _servicio():
    import proyecto.servicio as servicio

    return servicio


def _reiniciar() -> None:
    try:
        _servicio().reiniciar_orquestador()
    except Exception:  # noqa: BLE001 - sin orquestador previo no hay qué limpiar
        pass


def _vaciar_contexto_documental() -> None:
    """Higiene entre casos: los documentos de un caso no pasan al siguiente.

    ``conftest.py`` apunta ``DOCUMENT_CONTEXT_DB`` a un archivo temporal de la
    corrida, así que esto nunca toca el contexto real del usuario.
    """
    import sqlite3

    ruta = os.environ.get("DOCUMENT_CONTEXT_DB", "").strip()
    if not ruta or not os.environ.get("DEEPEVAL_CONTEXTO_TEMPORAL"):
        return
    if not Path(ruta).is_file():
        return
    with sqlite3.connect(ruta) as conexion:
        for tabla in ("active_documents", "documents"):
            try:
                conexion.execute(f"DELETE FROM {tabla}")
            except sqlite3.OperationalError:
                continue


def _enviar(pregunta: str, adjunto: str | None) -> dict:
    """Envía un turno igual que la UI: texto, imagen o documento."""
    servicio = _servicio()
    if not adjunto:
        return servicio.preguntar_orquestador(pregunta)
    ruta, nombre = preparar_adjunto(adjunto)
    if ruta.suffix.lower() in _EXTENSIONES_IMAGEN:
        return servicio.procesar_imagen_auto(str(ruta), pregunta, nombre)
    return servicio.procesar_documento(str(ruta), pregunta, nombre)


# ---------------------------------------------------------- comprobaciones


def _ruta_bd() -> Path | None:
    try:
        from sqlalchemy.engine import make_url

        from proyecto.agents.database import get_database_url

        url = make_url(get_database_url())
        return Path(url.database).resolve() if url.drivername == "sqlite" else None
    except Exception:  # noqa: BLE001
        return None


def _hash_archivo(ruta: Path | None) -> str | None:
    if ruta is None or not ruta.is_file():
        return None
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


@contextmanager
def _entorno(variables: dict[str, str]):
    """Variables solo durante el caso; los subprocesos MCP las heredan."""
    anteriores = {k: os.environ.get(k) for k in variables}
    os.environ.update(variables)
    try:
        yield
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor


# --------------------------------------------------------------- resultado


def _contexto(trazas: list[dict], respuesta: str) -> list[str]:
    """Convierte las salidas de las herramientas en evidencia para el juez.

    Normatividad y Data Analyst pueden devolver su respuesta ya sintetizada y
    el grafo la publica tal cual. Esa salida no es evidencia independiente
    (compararla contra sí misma siempre daría 1.0), así que se descarta.
    """
    fragmentos: list[str] = []
    for traza in trazas:
        salida = str(traza.get("salida") or "").strip()
        if not salida or traza.get("estado") == "error":
            continue
        if salida == respuesta.strip():
            continue
        # Normatividad separa cada documento recuperado con '---'.
        for parte in salida.split("\n\n---\n\n"):
            parte = parte.strip()
            if parte:
                fragmentos.append(parte[:MAX_CARACTERES_CONTEXTO])
    return fragmentos


def _normalizar(crudo: dict, duracion: float) -> ResultadoAgente:
    trazas = [t for t in (crudo.get("resultados") or []) if isinstance(t, dict)]
    errores = [str(crudo["error"])] if crudo.get("error") else []
    respuesta = str(crudo.get("respuesta") or "")
    return ResultadoAgente(
        respuesta=respuesta,
        agentes=list(crudo.get("agentes") or []),
        herramientas=list(crudo.get("herramientas") or []),
        contexto=_contexto(trazas, respuesta),
        trazas=trazas,
        fuentes=list(crudo.get("fuentes") or []),
        sql=crudo.get("sql"),
        datos=crudo.get("datos"),
        tiene_grafica=bool(crudo.get("grafica")),
        errores=errores,
        alertas=[str(alerta) for alerta in crudo.get("alertas") or []],
        duracion=round(duracion, 3),
    )


def _turno(turno: Turno, medicion) -> None:
    if turno.limpiar:
        inicio = time.perf_counter()
        _reiniciar()
        medicion.turno("limpiar", time.perf_counter() - inicio)
    if not (turno.pregunta or turno.adjunto):
        return
    inicio = time.perf_counter()
    crudo = _enviar(turno.pregunta, turno.adjunto)
    medicion.turno("previo", time.perf_counter() - inicio, crudo)


def ejecutar(caso: Caso) -> ResultadoAgente:
    """Ejecuta un caso en una conversación limpia y normaliza el resultado."""
    ruta_bd = _ruta_bd() if caso.bd_sin_cambios else None
    hash_antes = _hash_archivo(ruta_bd)

    with medir(caso.id) as medicion, _entorno(caso.entorno):
        huella_antes = None
        crudo: dict = {}
        duracion = 0.0
        # Un turno usa su propia `identidad` o, si no la declara, la del caso;
        # la pregunta final siempre usa la del caso. Turnos consecutivos con la
        # misma identidad comparten sesión; cambiar de identidad (US-08) cierra
        # la sesión anterior y abre otra.
        bloques: list[tuple[str | None, list[Turno]]] = []
        for turno in caso.turnos_previos:
            identidad = turno.identidad or caso.identidad
            if not bloques or bloques[-1][0] != identidad:
                bloques.append((identidad, []))
            bloques[-1][1].append(turno)
        if not bloques or bloques[-1][0] != caso.identidad:
            bloques.append((caso.identidad, []))

        _reiniciar()
        _vaciar_contexto_documental()
        for posicion, (identidad, turnos) in enumerate(bloques):
            with REGISTRO.sesion(identidad):
                for turno in turnos:
                    _turno(turno, medicion)
                if posicion < len(bloques) - 1:
                    continue
                if caso.identidad_sin_cambios:
                    huella_antes = REGISTRO.huella(identidad)
                inicio = time.perf_counter()
                crudo = _enviar(caso.pregunta, caso.adjunto)
                duracion = time.perf_counter() - inicio
                medicion.turno("final", duracion, crudo)

        resultado = _normalizar(crudo, duracion)
        if caso.identidad_sin_cambios:
            resultado.identidad_cambio = huella_antes != REGISTRO.huella(caso.identidad)

    if caso.bd_sin_cambios:
        resultado.bd_cambio = hash_antes is None or hash_antes != _hash_archivo(ruta_bd)
    resultado.rendimiento = medicion.datos
    return resultado
