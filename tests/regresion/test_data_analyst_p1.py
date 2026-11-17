from __future__ import annotations

import json

from sqlalchemy import create_engine

from proyecto.agents.banking_queries import format_result
from proyecto.agents.database import get_database_url
from proyecto.mcp_servers.data_analyst_backend import (
    _respuesta_campos_inexistentes,
    _respuesta_terminal,
)
from proyecto.mcp_servers.sql_guard import validar_sql


def _engine():
    return create_engine(get_database_url())


def test_falta_filtro_rls_es_corregible():
    resultado = validar_sql(
        "SELECT tipo_persona, COUNT(*) FROM vw_cliente_publico "
        "GROUP BY tipo_persona",
        engine=_engine(),
        perfil={"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok
    assert resultado.tipo_error == "alcance_corregible"
    assert any("num_cte" in error for error in resultado.errores)


def test_cliente_ajeno_sigue_siendo_denegacion():
    resultado = validar_sql(
        "SELECT COUNT(*) FROM vw_cliente_publico WHERE num_cte = 999999",
        engine=_engine(),
        perfil={"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok
    assert resultado.tipo_error == "autorizacion_denegada"


def test_respuesta_terminal_no_termina_alcance_corregible():
    assert (
        _respuesta_terminal(
            "ejecutar_consulta",
            json.dumps(
                {"tipo_error": "alcance_corregible", "error": "falta num_cte"}
            ),
        )
        is None
    )

    assert _respuesta_terminal(
        "ejecutar_consulta",
        json.dumps(
            {"tipo_error": "autorizacion_denegada", "error": "otro cliente"}
        ),
    ) == "Acceso denegado: tu identidad no tiene permiso para consultar esos datos."


def test_format_result_redondea_flotantes():
    texto = format_result(
        "Prueba",
        {"columnas": ["saldo_promedio"], "filas": [[38166.292499999996]]},
        "SELECT 1",
    )

    assert "38166.29" in texto
    assert "38166.292499999996" not in texto


def test_campo_salarial_inexistente_responde_sin_columna():
    respuesta = _respuesta_campos_inexistentes(
        ["sueldo"], {"saldo_mes", "saldo_txn", "importe_firmado"}
    )

    assert respuesta is not None
    assert "sueldo" in respuesta
    assert "saldo_mes" in respuesta


def test_campo_salarial_existente_no_bloquea():
    assert _respuesta_campos_inexistentes(["sueldo"], {"sueldo_base"}) is None
