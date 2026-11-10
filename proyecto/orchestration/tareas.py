"""Contratos compartidos entre el orquestador y los hubs de dominio.

Una tarea describe un resultado verificable. El hub decide qué herramientas
usar y cuántas veces; el orquestador solo planifica, ordena y sintetiza.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DominioTarea(str, Enum):
    """Especialista que puede ejecutar una tarea."""

    DATA_STEWARD = "data_steward"
    DATA_ANALYST = "data_analyst"
    NORMATIVIDAD_RAG = "normatividad_rag"
    DOCUMENT_INTELLIGENCE = "document_intelligence"
    COMPLIANCE = "compliance"


class EstadoResultado(str, Enum):
    """Resultado de una tarea vista por el orquestador."""

    OK = "ok"
    ERROR = "error"
    INCOMPLETO = "incompleto"
    REQUIERE_ACLARACION = "requiere_aclaracion"


class Tarea(BaseModel):
    """Instrucción de alto nivel que el orquestador delega a un hub.

    El hub no recibe el prompt literal del usuario: recibe la instrucción que
    redacta el orquestador y el contexto curado. La tarea no contiene nombres de
    herramientas, SQL ni prompts de especialista.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=120)
    dominio: DominioTarea
    instruccion: str = Field(min_length=1)
    objetivo: str = Field(min_length=1)
    resultado_esperado: str = Field(min_length=1)
    subobjetivos: list[str] = Field(default_factory=list)
    contexto: str = ""
    restricciones: list[str] = Field(default_factory=list)
    criterios: list[str] = Field(default_factory=list)
    evidencia_requerida: list[str] = Field(default_factory=list)
    depende_de: list[str] = Field(default_factory=list)
    prioridad: int = 0
    metadatos: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "subobjetivos", "restricciones", "criterios", "evidencia_requerida"
    )
    @classmethod
    def _sin_elementos_vacios(cls, valores: list[str]) -> list[str]:
        limpios = [valor.strip() for valor in valores if valor and valor.strip()]
        if len(limpios) != len(valores):
            raise ValueError("Los elementos de texto no pueden estar vacíos.")
        return limpios

    @model_validator(mode="after")
    def _sin_autodependencia(self) -> Tarea:
        if self.id in self.depende_de:
            raise ValueError(f"La tarea {self.id!r} no puede depender de sí misma.")
        return self


class ResultadoTarea(BaseModel):
    """Salida estructurada de un hub para una tarea."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tarea_id: str = Field(min_length=1)
    estado: EstadoResultado
    respuesta: str = ""
    datos: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    fuentes: list[str] = Field(default_factory=list)
    grafica: bytes | None = None
    grafica_especificacion: dict[str, Any] = Field(default_factory=dict)
    trazas: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: int = Field(default=0, ge=0)
    duracion: float | None = Field(default=None, ge=0)
    error: str | None = None
    evidencia: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _explicar_estado_no_ok(self) -> ResultadoTarea:
        if self.estado is not EstadoResultado.OK and not (self.error or self.respuesta):
            raise ValueError(
                "Un resultado no exitoso debe incluir 'error' o 'respuesta'."
            )
        return self


class ContextoHub(BaseModel):
    """Contexto curado que el orquestador entrega a un hub.

    No incluye el historial crudo de la conversación ni el prompt original del
    usuario. El orquestador decide qué parte del estado necesita el hub.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1)
    perfil: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    ultima_sql: str | None = None
    ultimo_resultado: dict[str, Any] | None = None
    ultimo_agente: str | None = None
    ultima_intencion: str | None = None


class ContextoSesion(BaseModel):
    """Estado completo que conserva el orquestador.

    Es la fuente de verdad para seguimientos: los slots y el último resultado
    sustituyen la relectura de todo el historial. El historial y los documentos
    se quedan en el orquestador; al hub se le envía un ``ContextoHub`` curado.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1)
    perfil: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    historial_reciente: list[dict[str, Any]] = Field(default_factory=list)
    ultima_sql: str | None = None
    ultimo_resultado: dict[str, Any] | None = None
    ultimo_agente: str | None = None
    ultima_intencion: str | None = None
    documentos: list[dict[str, Any]] = Field(default_factory=list)
    metadatos: dict[str, Any] = Field(default_factory=dict)

    def para_hub(self) -> ContextoHub:
        """Proyecta solo el estado que el hub necesita."""
        return ContextoHub(
            session_id=self.session_id,
            perfil=self.perfil,
            slots=self.slots,
            ultima_sql=self.ultima_sql,
            ultimo_resultado=self.ultimo_resultado,
            ultimo_agente=self.ultimo_agente,
            ultima_intencion=self.ultima_intencion,
        )


class PlanTareas(BaseModel):
    """Conjunto de tareas con dependencias válidas y orden de ejecución."""

    model_config = ConfigDict(extra="forbid")

    tareas: list[Tarea] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validar_plan(self) -> PlanTareas:
        ids = [tarea.id for tarea in self.tareas]
        duplicados = sorted(
            {identificador for identificador in ids if ids.count(identificador) > 1}
        )
        if duplicados:
            raise ValueError(f"IDs de tarea duplicados: {', '.join(duplicados)}")

        conocidos = set(ids)
        for tarea in self.tareas:
            faltantes = sorted(set(tarea.depende_de) - conocidos)
            if faltantes:
                raise ValueError(
                    f"La tarea {tarea.id!r} depende de tareas inexistentes: "
                    f"{', '.join(faltantes)}"
                )

        self._validar_ciclos()
        return self

    def _validar_ciclos(self) -> None:
        por_id = {tarea.id: tarea for tarea in self.tareas}
        estado: dict[str, int] = {}

        def visitar(tarea_id: str) -> None:
            if estado.get(tarea_id) == 1:
                raise ValueError(f"El plan contiene un ciclo en la tarea {tarea_id!r}.")
            if estado.get(tarea_id) == 2:
                return
            estado[tarea_id] = 1
            for dependencia in por_id[tarea_id].depende_de:
                visitar(dependencia)
            estado[tarea_id] = 2

        for tarea in self.tareas:
            visitar(tarea.id)

    def orden_ejecucion(self) -> list[Tarea]:
        """Devuelve las tareas en orden topológico, prioridad y orden original."""
        por_id = {tarea.id: tarea for tarea in self.tareas}
        posicion = {tarea.id: indice for indice, tarea in enumerate(self.tareas)}
        pendientes = {tarea.id: set(tarea.depende_de) for tarea in self.tareas}
        orden: list[Tarea] = []

        while pendientes:
            listas = [
                tarea_id
                for tarea_id, dependencias in pendientes.items()
                if not dependencias
            ]
            if not listas:
                raise ValueError("El plan contiene un ciclo y no se puede ordenar.")
            listas.sort(
                key=lambda tarea_id: (-por_id[tarea_id].prioridad, posicion[tarea_id])
            )
            for tarea_id in listas:
                orden.append(por_id[tarea_id])
                del pendientes[tarea_id]
            for dependencias in pendientes.values():
                dependencias.difference_update(listas)

        return orden


class HubDominio(Protocol):
    """Interfaz que implementa cada subagente de dominio."""

    nombre: str

    def ejecutar(self, tarea: Tarea, contexto: ContextoHub) -> ResultadoTarea:
        """Ejecuta una tarea y devuelve su resultado estructurado."""
        ...


__all__ = [
    "ContextoHub",
    "ContextoSesion",
    "DominioTarea",
    "EstadoResultado",
    "HubDominio",
    "PlanTareas",
    "ResultadoTarea",
    "Tarea",
]
