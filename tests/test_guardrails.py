"""Pruebas de los guardrails de texto: PII y cifras."""

from __future__ import annotations

import json

from proyecto.guardrails import limpiar_base64, redactar_pii, verificar_cifras
from proyecto.orchestration.hubs import HubDataAnalyst
from proyecto.orchestration.tareas import EstadoResultado, ResultadoTarea


def test_redacta_pii_evidente():
    texto = (
        "Contacto: ana@example.com, tel. 55-1234-5678, "
        "CURP AABD800101HDFXYZ01, RFC AABD800101XYZ."
    )
    redactado, tipos = redactar_pii(texto)

    assert "ana@example.com" not in redactado
    assert "55-1234-5678" not in redactado
    assert "AABD800101HDFXYZ01" not in redactado
    assert {"correo", "telefono", "curp", "rfc"} <= set(tipos)


def test_limpiar_base64_quita_data_uri_y_b64_largo():
    texto = (
        "Gráfica: ![x](data:image/png;base64,"
        + "A" * 700
        + ") y fin."
    )
    limpio = limpiar_base64(texto)

    assert "data:image" not in limpio
    assert "A" * 700 not in limpio
    assert "[gráfica generada]" in limpio
    assert limpio.endswith("y fin.")


def test_verificar_cifras_coincide_por_valor_numerico():
    assert verificar_cifras("Hay 1,000 clientes.", "total: 1000") == []
    assert verificar_cifras("El saldo fue 12,345.67.", "saldo 12345.67") == []


def test_verificar_cifras_reporta_no_verificadas():
    assert verificar_cifras("Hay 9,999 clientes.", "total: 1000") == ["9999"]


def test_verificar_cifras_ignora_anios_y_numeros_pequenos():
    assert verificar_cifras("En 2026 hubo 3 segmentos.", "nada") == []


def test_hub_redacta_respuesta_y_marca_cifras_no_verificadas():
    resultado = ResultadoTarea(
        tarea_id="sql-1",
        estado=EstadoResultado.OK,
        respuesta="Hay 9,999 clientes; escribe a ana@example.com.",
        trazas=[
            {
                "name": "ejecutar_consulta",
                "args": {"sql": "SELECT COUNT(*) AS n FROM dim_cliente"},
                "salida": json.dumps(
                    {"columnas": ["n"], "filas": [[10]], "truncado": False}
                ),
                "estado": "ok",
            }
        ],
    )

    refinado = HubDataAnalyst._contrastar_con_sql_ejecutado(resultado)

    assert "ana@example.com" not in refinado.respuesta
    assert "DATO PROTEGIDO" in refinado.respuesta
    assert refinado.evidencia["cifras_no_verificadas"] == ["9999"]
    assert "correo" in refinado.evidencia["pii_redactada"]
