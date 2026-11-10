"""Pruebas del puente opcional entre el grafo y HubDataAnalyst."""

from __future__ import annotations

import importlib

from proyecto.orchestration.tareas import (
    EstadoResultado,
    ResultadoTarea,
)

grafo = importlib.import_module("proyecto.orchestration.grafo")


class HubFalso:
    def ejecutar(self, tarea, contexto):
        assert tarea.instruccion == "¿Cuántos clientes hay?"
        assert contexto.perfil == {"rol": "usuario", "cliente_id": 1}
        return ResultadoTarea(
            tarea_id=tarea.id,
            estado=EstadoResultado.OK,
            respuesta="Hay 5,000 clientes.",
            sql="SELECT COUNT(*) FROM dim_cliente",
            datos={"columnas": ["COUNT(*)"], "filas": [[5000]]},
            trazas=[
                {
                    "name": "ejecutar_consulta",
                    "args": {"sql": "SELECT COUNT(*) FROM dim_cliente"},
                    "salida": '{"columnas": ["COUNT(*)"], "filas": [[5000]]}',
                    "estado": "ok",
                }
            ],
            fuentes=["banamex.db"],
        )


def test_grafo_puede_usar_hub_data_analyst(monkeypatch):
    monkeypatch.setenv("DATA_ANALYST_HUB", "1")
    monkeypatch.setattr(grafo, "_get_hub_data_analyst", lambda: HubFalso())

    resultado = grafo._nodo_data_analyst(
        {
            "consulta": "¿Cuántos clientes hay?",
            "perfil": {"rol": "usuario", "cliente_id": 1},
        }
    )

    datos = resultado["resultados"]["data_analyst"]
    assert datos["estado"] == "ok"
    assert datos["sql"] == "SELECT COUNT(*) FROM dim_cliente"
    assert datos["evidencia"] == {}
    assert resultado["errores"] == []
    assert resultado["trazas"][0]["name"] == "ejecutar_consulta"


def test_grafo_reporta_error_del_hub(monkeypatch):
    class HubError:
        def ejecutar(self, tarea, contexto):
            return ResultadoTarea(
                tarea_id=tarea.id,
                estado=EstadoResultado.ERROR,
                error="La consulta no pasó la validación.",
            )

    monkeypatch.setenv("DATA_ANALYST_HUB", "1")
    monkeypatch.setattr(grafo, "_get_hub_data_analyst", lambda: HubError())

    resultado = grafo._nodo_data_analyst(
        {
            "consulta": "consulta inválida",
            "perfil": {},
        }
    )

    assert resultado["resultados"]["data_analyst"]["estado"] == "error"
    assert "validación" in resultado["errores"][0]
