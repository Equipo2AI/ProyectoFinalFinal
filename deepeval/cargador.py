"""Lee los casos de prueba desde ``deepeval/casos/*.yaml``.

Las preguntas viven en YAML para que agregar, cambiar o borrar un caso no
requiera tocar código. El formato se documenta en ``deepeval/README.md``.

Cada caso declara qué historias de usuario y criterios de aceptación cubre
(``cubre: [US-03.CA-02]``). Con eso la suite genera etiquetas por historia
(``-m us03``) y la matriz de cobertura del reporte de rendimiento.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

import yaml

CARPETA_CASOS = Path(
    os.getenv("DEEPEVAL_CASOS_DIR") or Path(__file__).resolve().parent / "casos"
)
ARCHIVO_HISTORIAS = Path(__file__).resolve().parent / "historias.yaml"

METRICAS_VALIDAS = {
    "reglas",
    "relevancia",
    "fidelidad",
    "correccion",
    "criterio",
    "latencia",
}

# US-03.CA-02  ->  historia US-03, criterio CA-02
_RE_COBERTURA = re.compile(r"^(US-\d{2})(?:\.(CA-\d{2}))?$")


@dataclass(frozen=True)
class Turno:
    """Un paso previo de la conversación.

    ``pregunta`` se envía al orquestador; con ``adjunto`` se procesa como
    archivo (igual que la UI). ``limpiar`` reproduce el botón "Limpiar" y
    ``identidad`` cambia de sesión antes de ese turno.
    """

    pregunta: str = ""
    adjunto: str | None = None
    limpiar: bool = False
    identidad: str | None = None


@dataclass(frozen=True)
class Caso:
    id: str
    pregunta: str
    archivo: str = ""
    # Cobertura: historias y criterios de aceptación, p. ej. "US-01.CA-03".
    cubre: tuple[str, ...] = ()
    # Identidad aprovisionada en deepeval/identidades.yaml ("sin_sesion" para
    # probar sin login). El rol y los permisos los decide el operador, no el caso.
    identidad: str | None = None
    # Compatibilidad con casos antiguos; el servicio no concede permisos por él.
    perfil: dict = field(default_factory=dict)
    # Turnos que se ejecutan antes de la pregunta, en la misma conversación.
    turnos_previos: tuple[Turno, ...] = ()
    # Archivo que acompaña a la pregunta final ("fixture:nombre" o ruta).
    adjunto: str | None = None
    # Variables de entorno solo durante el caso (p. ej. desactivar Pinecone).
    entorno: dict = field(default_factory=dict)
    # Verificaciones deterministas (no usan LLM).
    agente_esperado: str | None = None
    agentes_esperados: tuple[str, ...] = ()
    herramientas_esperadas: tuple[str, ...] = ()
    debe_contener: tuple[str, ...] = ()
    debe_contener_alguno: tuple[str, ...] = ()
    no_debe_contener: tuple[str, ...] = ()
    error_esperado: str | None = None  # el error/denegación ES el resultado correcto
    alertas_esperadas: tuple[str, ...] = ()  # alertas permitidas (por subcadena)
    debe_tener_sql: bool | None = None
    debe_tener_grafica: bool | None = None
    debe_tener_fuentes: bool | None = None
    max_filas: int | None = None
    sin_pii: bool = False  # respuesta y trazas sin correo/teléfono/CURP/RFC
    traza_completa: bool = False  # cada herramienta registra nombre y estado
    bd_sin_cambios: bool = False  # la base sintética no cambia de hash
    identidad_sin_cambios: bool = False  # rol/permisos de la identidad intactos
    # Rendimiento: presupuesto de tiempo del agente (activa "latencia").
    max_segundos: float | None = None
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

    @property
    def historias(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.split(".")[0] for c in self.cubre))


_CAMPOS = {f.name for f in fields(Caso)} - {"archivo"}
_CAMPOS_LISTA = {
    "cubre",
    "agentes_esperados",
    "herramientas_esperadas",
    "debe_contener",
    "debe_contener_alguno",
    "no_debe_contener",
    "alertas_esperadas",
    "metricas",
    "etiquetas",
}
_CAMPOS_TURNO = {f.name for f in fields(Turno)}


def _como_tupla(valor) -> tuple[str, ...]:
    if valor is None:
        return ()
    if isinstance(valor, (str, int, float)):
        return (str(valor),)
    return tuple(str(v) for v in valor)


def _turnos(valor, archivo: str, caso_id: str) -> tuple[Turno, ...]:
    turnos: list[Turno] = []
    for crudo in valor or []:
        if isinstance(crudo, str):
            turnos.append(Turno(pregunta=crudo))
            continue
        if not isinstance(crudo, dict):
            raise ValueError(f"{archivo}: turno inválido en {caso_id!r}: {crudo!r}")
        desconocidos = set(crudo) - _CAMPOS_TURNO
        if desconocidos:
            raise ValueError(
                f"{archivo}: campos de turno desconocidos {sorted(desconocidos)} "
                f"en {caso_id!r}. Válidos: {sorted(_CAMPOS_TURNO)}"
            )
        turno = Turno(
            pregunta=str(crudo.get("pregunta") or ""),
            adjunto=crudo.get("adjunto"),
            limpiar=bool(crudo.get("limpiar")),
            identidad=crudo.get("identidad"),
        )
        if not (turno.pregunta or turno.adjunto or turno.limpiar or turno.identidad):
            raise ValueError(f"{archivo}: turno vacío en {caso_id!r}")
        turnos.append(turno)
    return tuple(turnos)


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
    for obligatorio in ("id", "pregunta"):
        if datos.get(obligatorio) is None or (
            obligatorio == "id" and not str(datos.get("id")).strip()
        ):
            raise ValueError(f"{archivo}: falta '{obligatorio}' en {crudo!r}")
    caso_id = str(datos["id"]).strip()
    for mapa in ("perfil", "entorno"):
        valor = datos.get(mapa) or {}
        if not isinstance(valor, dict):
            raise ValueError(f"{archivo}: '{mapa}' debe ser un mapa en {caso_id!r}")
        datos[mapa] = {str(k): ("" if v is None else str(v)) for k, v in valor.items()} \
            if mapa == "entorno" else dict(valor)
    for campo in _CAMPOS_LISTA:
        datos[campo] = _como_tupla(datos.get(campo))
    datos["turnos_previos"] = _turnos(datos.get("turnos_previos"), archivo, caso_id)

    for item in datos["cubre"]:
        if not _RE_COBERTURA.match(item):
            raise ValueError(
                f"{archivo}: cobertura inválida {item!r} en {caso_id!r}; "
                "usa 'US-03' o 'US-03.CA-02'"
            )

    # Etiquetas = archivo + caso + una por historia cubierta (us01, us02…).
    etiquetas = _como_tupla(predeterminados.get("etiquetas")) + _como_tupla(
        crudo.get("etiquetas")
    )
    etiquetas += tuple(h.replace("-", "").lower() for h in Caso(
        id="x", pregunta="x", cubre=datos["cubre"]).historias)
    datos["etiquetas"] = tuple(dict.fromkeys(etiquetas))

    invalidas = set(datos["metricas"]) - METRICAS_VALIDAS
    if invalidas:
        raise ValueError(
            f"{archivo}: métricas desconocidas {sorted(invalidas)} en "
            f"{caso_id!r}. Válidas: {sorted(METRICAS_VALIDAS)}"
        )
    datos["id"] = caso_id
    datos["pregunta"] = str(datos["pregunta"]).strip()
    datos["umbral"] = float(datos.get("umbral", 0.5))
    if datos.get("max_segundos") is not None:
        datos["max_segundos"] = float(datos["max_segundos"])
    if datos.get("max_filas") is not None:
        datos["max_filas"] = int(datos["max_filas"])
    for booleano_o_texto in ("xfail", "skip"):
        valor = datos.get(booleano_o_texto)
        datos[booleano_o_texto] = (
            None if not valor else ("sin motivo" if valor is True else str(valor))
        )
    if not datos["pregunta"] and not datos.get("adjunto"):
        raise ValueError(f"{archivo}: {caso_id!r} necesita 'pregunta' o 'adjunto'")
    return Caso(archivo=archivo, **datos)


@lru_cache(maxsize=1)
def catalogo_historias() -> dict[str, dict]:
    """Historias y criterios de ``historias.yaml`` (vacío si no existe)."""
    if not ARCHIVO_HISTORIAS.is_file():
        return {}
    datos = yaml.safe_load(ARCHIVO_HISTORIAS.read_text(encoding="utf-8")) or {}
    return datos.get("historias") or {}


def _validar_cobertura(caso: Caso) -> None:
    catalogo = catalogo_historias()
    if not catalogo:
        return
    for item in caso.cubre:
        historia, _, criterio = item.partition(".")
        if historia not in catalogo:
            raise ValueError(f"{caso.archivo}: {caso.id!r} cubre {historia}, que no existe en historias.yaml")
        if criterio and criterio not in (catalogo[historia].get("criterios") or {}):
            raise ValueError(f"{caso.archivo}: {caso.id!r} cubre {item}, que no existe en historias.yaml")


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
            _validar_cobertura(caso)
            if caso.id in vistos:
                raise ValueError(
                    f"id repetido {caso.id!r}: {vistos[caso.id]} y {ruta.name}"
                )
            vistos[caso.id] = ruta.name
            casos.append(caso)
    return tuple(casos)
