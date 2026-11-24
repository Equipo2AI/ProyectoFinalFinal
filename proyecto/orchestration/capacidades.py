"""Contratos de capacidades y pasos del orquestador.

Este módulo es deliberadamente determinista y sin dependencias del grafo. El
planificador puede razonar sobre el catálogo, pero solo puede ejecutar
capacidades registradas, de solo lectura y con dependencias válidas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class PlanInvalido(ValueError):
    """El plan propuesto no cumple el contrato de capacidades."""


@dataclass(frozen=True)
class Capacidad:
    """Una acción que el orquestador puede pedir a un especialista."""

    nombre: str
    agente: str
    descripcion: str
    entrada: dict[str, str] = field(default_factory=dict)
    salida: dict[str, str] = field(default_factory=dict)
    permisos: tuple[str, ...] = ("usuario",)
    efectos: str = "lectura"
    requiere: tuple[str, ...] = ()

    def a_dict(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "agente": self.agente,
            "descripcion": self.descripcion,
            "entrada": self.entrada,
            "salida": self.salida,
            "permisos": list(self.permisos),
            "efectos": self.efectos,
            "requiere": list(self.requiere),
        }


@dataclass(frozen=True)
class PasoPlan:
    """Un paso concreto del plan, con dependencias entre pasos."""

    id: str
    capacidad: str
    args: dict[str, Any] = field(default_factory=dict)
    depende_de: tuple[str, ...] = ()
    proposito: str = ""

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "PasoPlan":
        if not isinstance(datos, dict):
            raise PlanInvalido("Cada paso debe ser un objeto JSON.")
        paso_id = str(datos.get("id") or "").strip()
        capacidad = str(datos.get("capacidad") or "").strip()
        if not paso_id or not capacidad:
            raise PlanInvalido("Cada paso necesita id y capacidad.")
        args = datos.get("args") or {}
        if not isinstance(args, dict):
            raise PlanInvalido(f"Los args del paso {paso_id} deben ser un objeto.")
        depende = datos.get("depende_de") or []
        if isinstance(depende, str):
            depende = [depende]
        if not isinstance(depende, list):
            raise PlanInvalido(
                f"depende_de del paso {paso_id} debe ser una lista."
            )
        return cls(
            id=paso_id,
            capacidad=capacidad,
            args=dict(args),
            depende_de=tuple(str(item).strip() for item in depende if str(item).strip()),
            proposito=str(datos.get("proposito") or "").strip(),
        )

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "capacidad": self.capacidad,
            "args": dict(self.args),
            "depende_de": list(self.depende_de),
            "proposito": self.proposito,
        }


CAPACIDADES: dict[str, Capacidad] = {
    "datos.metadatos": Capacidad(
        nombre="datos.metadatos",
        agente="data_steward",
        descripcion=(
            "Consulta estructura, tablas, columnas, nulos, duplicados, "
            "relaciones y calidad de la base sintética."
        ),
        entrada={"consulta": "str"},
        salida={"respuesta": "str", "estado": "ok|error"},
        permisos=("usuario", "governance"),
        requiere=("consulta",),
    ),
    "datos.metricas": Capacidad(
        nombre="datos.metricas",
        agente="data_analyst",
        descripcion=(
            "Calcula métricas, saldos, conteos, promedios, rankings y "
            "consultas SQL de solo lectura sobre la base."
        ),
        entrada={"consulta": "str", "contexto": "str"},
        salida={"respuesta": "str", "sql": "str?", "estado": "ok|error"},
        permisos=("usuario",),
        requiere=("consulta",),
    ),
    "normativa.buscar": Capacidad(
        nombre="normativa.buscar",
        agente="normatividad_rag",
        descripcion=(
            "Recupera políticas, leyes, manuales y requisitos desde el corpus "
            "normativo, con fuentes y páginas literales."
        ),
        entrada={"query": "str"},
        salida={"respuesta": "str", "fuentes": "list"},
        permisos=("usuario",),
        requiere=("consulta",),
    ),
    "documentos.leer": Capacidad(
        nombre="documentos.leer",
        agente="document_intelligence",
        descripcion=(
            "Convierte una imagen o un documento local a texto/Markdown. "
            "Requiere una ruta explícita en la consulta o una imagen pendiente; "
            "no lee por sí sola el documento activo del usuario."
        ),
        entrada={"file_path": "str?"},
        salida={"respuesta": "str", "estado": "ok|error"},
        permisos=("usuario",),
        requiere=("ruta_documento|imagen_pendiente",),
    ),
}

CAPACIDAD_POR_AGENTE: dict[str, str] = {
    capacidad.agente: nombre
    for nombre, capacidad in CAPACIDADES.items()
}

# Los agentes actuales se conservan como adaptadores durante la migración. El
# catálogo puede crecer con capacidades más finas sin agregar nodos al grafo.
AGENTES_EJECUTABLES = frozenset(CAPACIDAD_POR_AGENTE)


def capacidad(nombre: str) -> Capacidad:
    try:
        return CAPACIDADES[nombre]
    except KeyError as error:
        raise PlanInvalido(f"Capacidad desconocida: {nombre}") from error


def pasos_para_agentes(agentes: Iterable[str]) -> list[PasoPlan]:
    """Adapta las rutas deterministas actuales a pasos con contrato."""
    pasos: list[PasoPlan] = []
    for agente in agentes:
        nombre = CAPACIDAD_POR_AGENTE.get(str(agente))
        if not nombre:
            continue
        if any(paso.capacidad == nombre for paso in pasos):
            continue
        pasos.append(
            PasoPlan(
                id=f"paso_{len(pasos) + 1}_{nombre.replace('.', '_')}",
                capacidad=nombre,
                proposito=f"Ejecutar {agente} para la consulta actual.",
            )
        )
    return pasos


def agentes_de_pasos(pasos: Iterable[PasoPlan]) -> list[str]:
    agentes: list[str] = []
    for paso in pasos:
        agente = capacidad(paso.capacidad).agente
        if agente not in agentes:
            agentes.append(agente)
    return agentes


def validar_pasos(pasos: Iterable[PasoPlan]) -> list[PasoPlan]:
    """Valida ids, capacidades, efectos y dependencias; devuelve el plan."""
    lista = list(pasos)
    if not lista:
        return []

    ids: set[str] = set()
    for paso in lista:
        if paso.id in ids:
            raise PlanInvalido(f"Id de paso duplicado: {paso.id}")
        ids.add(paso.id)
        cap = capacidad(paso.capacidad)
        if cap.efectos != "lectura":
            raise PlanInvalido(
                f"La capacidad {cap.nombre} no es de solo lectura."
            )

    for paso in lista:
        for dependencia in paso.depende_de:
            if dependencia not in ids:
                raise PlanInvalido(
                    f"El paso {paso.id} depende de {dependencia}, que no existe."
                )
            if dependencia == paso.id:
                raise PlanInvalido(f"El paso {paso.id} no puede depender de sí mismo.")

    ordenados = ordenar_pasos(lista)
    return ordenados


def ordenar_pasos(pasos: Iterable[PasoPlan]) -> list[PasoPlan]:
    """Orden topológico estable; falla si hay ciclos."""
    lista = list(pasos)
    por_id = {paso.id: paso for paso in lista}
    orden: list[PasoPlan] = []
    visitados: set[str] = set()
    en_curso: set[str] = set()

    def visitar(paso_id: str) -> None:
        if paso_id in visitados:
            return
        if paso_id in en_curso:
            raise PlanInvalido(f"Ciclo de dependencias en el paso {paso_id}.")
        en_curso.add(paso_id)
        paso = por_id[paso_id]
        for dependencia in paso.depende_de:
            if dependencia not in por_id:
                raise PlanInvalido(
                    f"El paso {paso.id} depende de {dependencia}, que no existe."
                )
            visitar(dependencia)
        en_curso.remove(paso_id)
        visitados.add(paso_id)
        orden.append(paso)

    for paso in lista:
        visitar(paso.id)
    return orden


def resumen_para_prompt() -> str:
    """Catálogo compacto para el planner; no incluye código ni rutas."""
    return "\n".join(
        f"- {cap.nombre}: {cap.descripcion} "
        f"Entrada: {cap.entrada}. Salida: {cap.salida}. "
        f"Requiere: {cap.requiere or 'nada'}."
        for cap in CAPACIDADES.values()
    )
