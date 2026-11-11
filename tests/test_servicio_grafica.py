"""Prueba de integración del contrato de respuesta con gráfica y guardrails."""

from __future__ import annotations

from proyecto import servicio
from proyecto.charts import generar_grafica


def test_respuesta_expone_grafica_datos_sql_y_redacta_pii(monkeypatch, identidad_prueba):
    png = generar_grafica(
        ["segmento", "clientes"],
        [["Banca masiva", 10], ["Banca preferente", 5]],
        tipo="bar",
        eje_x="segmento",
        eje_y="clientes",
    )

    class OrquestadorFalso:
        ultimas_herramientas = [
            {
                "name": "mcp_analizar_datos",
                "args": {},
                "salida": "```sql\nSELECT segmento, COUNT(*) FROM vw_cliente_publico GROUP BY segmento\n```",
                "estado": "ok",
            }
        ]
        agentes_invocados = ["mcp_analizar_datos"]
        ultimo_estado_grafo = {
            "resultados": {
                "data_analyst": {
                    "sql": "SELECT segmento, COUNT(*) FROM vw_cliente_publico GROUP BY segmento",
                    "datos": {
                        "columnas": ["segmento", "clientes"],
                        "filas": [["Banca masiva", 10]],
                        "truncado": False,
                    },
                    "grafica": png["png"],
                    "grafica_especificacion": png["especificacion"],
                    "evidencia": {"cifras_no_verificadas": ["9999"]},
                }
            }
        }
        modo_efectivo = "grafo"
        modelo = "modelo-falso"

        def procesar(self, consulta, perfil=None):
            return (
                "Hay 9,999 clientes. Contacto: ana@example.com. "
                "![gráfica](data:image/png;base64," + "A" * 700 + ")"
            )

    monkeypatch.setattr(servicio, "_get_orquestador", lambda: OrquestadorFalso())
    respuesta = servicio.preguntar_orquestador(
        "Haz una gráfica de clientes por segmento",
        {"rol": "superusuario"},
    )

    assert respuesta["grafica"] == png["png"]
    assert respuesta["datos"] == [{"segmento": "Banca masiva", "clientes": 10}]
    assert respuesta["sql"].startswith("SELECT segmento")
    assert "ana@example.com" not in respuesta["respuesta"]
    assert "DATO PROTEGIDO" in respuesta["respuesta"]
    assert "data:image" not in respuesta["respuesta"]
    assert "[gráfica generada]" in respuesta["respuesta"]
    assert any("Cifras no encontradas" in alerta for alerta in respuesta["alertas"])
    assert any("datos personales" in alerta for alerta in respuesta["alertas"])
