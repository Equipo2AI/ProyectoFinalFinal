import pytest
from pydantic import ValidationError

from proyecto.orchestration.tareas import (
    ContextoSesion,
    DominioTarea,
    EstadoResultado,
    PlanTareas,
    ResultadoTarea,
    Tarea,
)


def _tarea(**cambios):
    datos = {
        "id": "t1",
        "dominio": DominioTarea.DATA_ANALYST,
        "instruccion": "Calcula el saldo del cliente 200 para febrero-marzo.",
        "objetivo": "Saldo del cliente 200 para febrero-marzo",
        "resultado_esperado": "Monto y desglose mensual con el SQL ejecutado",
    }
    datos.update(cambios)
    return Tarea(**datos)


def test_plan_ordena_por_dependencias_y_prioridad():
    plan = PlanTareas(
        tareas=[
            _tarea(id="sql", depende_de=["metadata"], prioridad=1),
            _tarea(id="metadata", prioridad=0),
            _tarea(id="rag", prioridad=5),
        ]
    )

    assert [tarea.id for tarea in plan.orden_ejecucion()] == [
        "rag",
        "metadata",
        "sql",
    ]


def test_plan_rechaza_ids_duplicados():
    with pytest.raises(ValidationError, match="duplicados"):
        PlanTareas(tareas=[_tarea(id="t1"), _tarea(id="t1")])


def test_plan_rechaza_dependencia_inexistente():
    with pytest.raises(ValidationError, match="inexistentes"):
        PlanTareas(tareas=[_tarea(id="t1", depende_de=["no-existe"])])


def test_plan_rechaza_ciclos():
    with pytest.raises(ValidationError, match="ciclo"):
        PlanTareas(
            tareas=[
                _tarea(id="a", depende_de=["b"]),
                _tarea(id="b", depende_de=["a"]),
            ]
        )


def test_tarea_rechaza_autodependencia():
    with pytest.raises(ValidationError, match="sí misma"):
        _tarea(id="t1", depende_de=["t1"])


def test_resultado_no_ok_exige_explicacion():
    with pytest.raises(ValidationError, match="error.*respuesta"):
        ResultadoTarea(tarea_id="t1", estado=EstadoResultado.ERROR)

    resultado = ResultadoTarea(
        tarea_id="t1",
        estado=EstadoResultado.ERROR,
        error="El hub agotó sus iteraciones.",
    )
    assert resultado.estado is EstadoResultado.ERROR


def test_contexto_sesion_tiene_defaults():
    contexto = ContextoSesion(session_id="ses-1")

    assert contexto.perfil == {}
    assert contexto.slots == {}
    assert contexto.historial_reciente == []
    assert contexto.documentos == []


def test_contexto_para_hub_no_expone_historial_ni_documentos():
    contexto = ContextoSesion(
        session_id="ses-1",
        slots={"cliente_id": 200},
        historial_reciente=[{"role": "user", "content": "texto crudo"}],
        documentos=[{"texto": "contenido OCR"}],
    )

    contexto_hub = contexto.para_hub()

    assert contexto_hub.session_id == "ses-1"
    assert contexto_hub.slots == {"cliente_id": 200}
    assert not hasattr(contexto_hub, "historial_reciente")
    assert not hasattr(contexto_hub, "documentos")
