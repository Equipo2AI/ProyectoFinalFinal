"""Lee los casos de prueba desde ``deepeval/casos/*.yaml``.

Las preguntas viven en YAML para que agregar, cambiar o borrar un caso no
requiera tocar código. El formato se documenta en ``deepeval/README.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

import yaml

CARPETA_CASOS = Path(
    os.getenv("DEEPEVAL_CASOS_DIR") or Path(__file__).resolve().parent / "casos"
)

METRICAS_VALIDAS = {"reglas", "relevancia", "fidelidad", "correccion", "criterio"}


@dataclass(frozen=True)
class Caso:
    id: str
    pregunta: str
    archivo: str = ""
    # Contexto de sesión: {"rol": "usuario"|"superusuario", "cliente_id": 1}
    perfil: dict = field(default_factory=dict)
    # Turnos que se envían antes de la pregunta, en la misma conversación.
    turnos_previos: tuple[str, ...] = ()
    # Verificaciones deterministas (no usan LLM).
    agente_esperado: str | None = None
    herramientas_esperadas: tuple[str, ...] = ()
    debe_contener: tuple[str, ...] = ()
    debe_contener_alguno: tuple[str, ...] = ()
    no_debe_contener: tuple[str, ...] = ()
    # Verificaciones con LLM juez.
    respuesta_esperada: str | None = None  # activa la métrica "correccion"
    criterio: str | None = None  # activa la métrica "criterio" (G-Eval libre)
    metricas: tuple[str, ...] = ()
    umbral: float = 0.5
    # Control de ejecución.
    etiquetas: tuple[str, ...] = ()
    xfail: str | None = None  # fallo conocido: se reporta sin romper la suite
    skip: str | None = None  # no se ejecuta

    def __str__(self) -> str:  # id legible en pytest
        return self.id


_CAMPOS = {f.name for f in fields(Caso)} - {"archivo"}
_CAMPOS_LISTA = {
    "turnos_previos",
    "herramientas_esperadas",
    "debe_contener",
    "debe_contener_alguno",
    "no_debe_contener",
    "metricas",
    "etiquetas",
}


def _como_tupla(valor) -> tuple[str, ...]:
    if valor is None:
        return ()
    if isinstance(valor, (str, int, float)):
        return (str(valor),)
    return tuple(str(v) for v in valor)


def _construir(crudo: dict, predeterminados: dict, archivo: str) -> Caso:
    if not isinstance(crudo, dict):
        raise ValueError(f"{archivo}: cada caso debe ser un mapa, no {crudo!r}")
    datos = {**predeterminados, **crudo}
    desconocidos = set(datos) - _CAMPOS
    if desconocidos:
        raise ValueError(
            f"{archivo}: campos desconocidos {sorted(desconocidos)} en el caso "
            f"{datos.get('id', '?')!r}. Válidos: {sorted(_CAMPOS)}"
        )
    perfil = datos.get("perfil") or {}
    if not isinstance(perfil, dict):
        raise ValueError(
            f"{archivo}: 'perfil' debe ser un mapa en {datos.get('id', '?')!r}"
        )
    datos["perfil"] = dict(perfil)
    for obligatorio in ("id", "pregunta"):
        if not str(datos.get(obligatorio) or "").strip():
            raise ValueError(f"{archivo}: falta '{obligatorio}' en {crudo!r}")
    for campo in _CAMPOS_LISTA:
        datos[campo] = _como_tupla(datos.get(campo))
    # Las etiquetas del archivo y las del caso se suman.
    datos["etiquetas"] = tuple(
        dict.fromkeys(
            _como_tupla(predeterminados.get("etiquetas")) + _como_tupla(crudo.get("etiquetas"))
        )
    )
    invalidas = set(datos["metricas"]) - METRICAS_VALIDAS
    if invalidas:
        raise ValueError(
            f"{archivo}: métricas desconocidas {sorted(invalidas)} en "
            f"{datos['id']!r}. Válidas: {sorted(METRICAS_VALIDAS)}"
        )
    datos["id"] = str(datos["id"]).strip()
    datos["pregunta"] = str(datos["pregunta"]).strip()
    datos["umbral"] = float(datos.get("umbral", 0.5))
    for booleano_o_texto in ("xfail", "skip"):
        valor = datos.get(booleano_o_texto)
        datos[booleano_o_texto] = (
            None if not valor else ("sin motivo" if valor is True else str(valor))
        )
    return Caso(archivo=archivo, **datos)


@lru_cache(maxsize=1)
def cargar_casos() -> tuple[Caso, ...]:
    """Devuelve todos los casos de la carpeta, validados y sin ids repetidos."""
    casos: list[Caso] = []
    vistos: dict[str, str] = {}
    archivos = sorted([*CARPETA_CASOS.glob("*.yaml"), *CARPETA_CASOS.glob("*.yml")])
    for ruta in archivos:
        contenido = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
        if isinstance(contenido, list):  # se admite una lista simple de casos
            contenido = {"casos": contenido}
        predeterminados = contenido.get("predeterminados") or {}
        for crudo in contenido.get("casos") or []:
            caso = _construir(crudo, predeterminados, ruta.name)
            if caso.id in vistos:
                raise ValueError(
                    f"id repetido {caso.id!r}: {vistos[caso.id]} y {ruta.name}"
                )
            vistos[caso.id] = ruta.name
            casos.append(caso)
    return tuple(casos)
