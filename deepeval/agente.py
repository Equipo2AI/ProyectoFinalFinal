"""Adaptador entre la suite y el agente.

ESTE ES EL ÚNICO ARCHIVO QUE CONOCE AL PROYECTO. Si cambia la forma de invocar
al agente (otro módulo, otra firma, otra estructura de respuesta), se ajusta
aquí y el resto de la suite, incluidos los YAML, sigue igual.

Hoy usa el mismo contrato que la interfaz Streamlit:
``proyecto.servicio.preguntar_orquestador(consulta) -> Respuesta``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cargador import Caso

# Tope por fragmento de evidencia que se envía al juez; las salidas JSON del
# Data Steward pueden ser muy largas para un modelo local.
MAX_CARACTERES_CONTEXTO = 4000


@dataclass
class ResultadoAgente:
    respuesta: str
    agentes: list[str] = field(default_factory=list)
    herramientas: list[str] = field(default_factory=list)
    contexto: list[str] = field(default_factory=list)  # salidas de herramientas
    fuentes: list[str] = field(default_factory=list)
    sql: str | None = None
    errores: list[str] = field(default_factory=list)
    duracion: float | None = None


def _preguntar(consulta: str, perfil: dict | None = None) -> dict:
    from proyecto.servicio import preguntar_orquestador

    return preguntar_orquestador(consulta, perfil=perfil)


def _reiniciar() -> None:
    from proyecto.servicio import reiniciar_orquestador

    reiniciar_orquestador()


def _contexto(trazas: list[dict], respuesta: str) -> list[str]:
    """Convierte las salidas de las herramientas en evidencia para el juez.

    Normatividad y Data Analyst devuelven su respuesta ya sintetizada, y el
    grafo la publica tal cual. Esa salida no es evidencia independiente
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


def ejecutar(caso: Caso) -> ResultadoAgente:
    """Ejecuta un caso en una conversación limpia y normaliza el resultado."""
    _reiniciar()
    for turno in caso.turnos_previos:
        _preguntar(turno, caso.perfil)
    crudo = _preguntar(caso.pregunta, caso.perfil)

    trazas = list(crudo.get("resultados") or [])
    errores = [str(crudo["error"])] if crudo.get("error") else []
    errores += [str(alerta) for alerta in crudo.get("alertas") or []]

    respuesta = str(crudo.get("respuesta") or "")
    return ResultadoAgente(
        respuesta=respuesta,
        agentes=list(crudo.get("agentes") or []),
        herramientas=list(crudo.get("herramientas") or []),
        contexto=_contexto(trazas, respuesta),
        fuentes=list(crudo.get("fuentes") or []),
        sql=crudo.get("sql"),
        errores=errores,
        duracion=crudo.get("duracion"),
    )
