from langchain_core.messages import AIMessage, ToolMessage

from proyecto.orchestration.hubs import (
    HubLangChain,
    SalidaHub,
    _contar_tool_calls,
    _salida_desde_mensajes,
    _trazas_de_mensajes,
)
from proyecto.orchestration.tareas import (
    ContextoSesion,
    DominioTarea,
    EstadoResultado,
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


def _hub():
    return HubLangChain(
        nombre="data_analyst",
        dominio=DominioTarea.DATA_ANALYST,
        tools=[],
        system_prompt="Eres un Data Analyst.",
        modelo="qwen2.5:7b-instruct",
    )


def test_construir_entrada_usa_instruccion_del_orquestador():
    contexto = ContextoSesion(
        session_id="ses-1",
        slots={"cliente_id": 200, "periodo": "2026-02..2026-03"},
        historial_reciente=[
            {"role": "user", "content": "texto crudo que no debe llegar al hub"}
        ],
        ultima_sql="SELECT 1",
    )

    entrada = _hub().construir_entrada(_tarea(), contexto.para_hub())

    assert "Calcula el saldo del cliente 200" in entrada
    assert "resultado_esperado: Monto y desglose mensual" in entrada
    assert "cliente_id" in entrada
    assert "SELECT 1" in entrada
    assert "texto crudo que no debe llegar al hub" not in entrada
    assert "TEXTO LITERAL DEL USUARIO" not in entrada


def test_hub_rechaza_tarea_de_otro_dominio():
    resultado = _hub().ejecutar(
        _tarea(dominio=DominioTarea.NORMATIVIDAD_RAG),
        ContextoSesion(session_id="ses-1").para_hub(),
    )

    assert resultado.estado is EstadoResultado.ERROR
    assert "no puede ejecutar" in resultado.error
    assert resultado.tool_calls == 0


def test_salida_hub_exige_respuesta():
    salida = SalidaHub(respuesta="Listo")

    assert salida.requiere_aclaracion is False
    assert salida.fuentes == []


def test_fallback_de_salida_acepta_texto_plano():
    salida = _salida_desde_mensajes([AIMessage(content="Hay 5,000 clientes.")])

    assert salida is not None
    assert salida.respuesta == "Hay 5,000 clientes."


def test_fallback_de_salida_acepta_json_estructurado():
    salida = _salida_desde_mensajes([
        AIMessage(content='{"respuesta": "Listo", "datos": {"total": 5}}')
    ])

    assert salida is not None
    assert salida.respuesta == "Listo"
    assert salida.datos == {"total": 5}


def test_trazas_emparejan_tool_calls_con_resultados():
    mensajes = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "describir_tabla",
                    "args": {"tabla": "vw_saldos"},
                    "id": "call-1",
                }
            ],
        ),
        ToolMessage(
            content='{"tabla": "vw_saldos"}',
            tool_call_id="call-1",
            name="describir_tabla",
        ),
    ]

    trazas = _trazas_de_mensajes(mensajes)

    assert _contar_tool_calls(mensajes) == 1
    assert trazas == [
        {
            "name": "describir_tabla",
            "args": {"tabla": "vw_saldos"},
            "salida": '{"tabla": "vw_saldos"}',
            "estado": "ok",
        }
    ]
