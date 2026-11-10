"""Pruebas del puente opcional de gráficas en el grafo, sin LLM."""

from __future__ import annotations

import base64
import json

from proyecto.charts import generar_grafica
from proyecto.orchestration import grafo


def _respuesta_con_sql() -> str:
    return (
        "Hay 15 clientes en la muestra.\n\n"
        "```sql\nSELECT segmento, COUNT(*) AS clientes "
        "FROM vw_cliente_publico GROUP BY segmento\n```"
    )


def _salida_grafica() -> str:
    png = generar_grafica(
        ["segmento", "clientes"],
        [["Banca masiva", 10], ["Banca preferente", 5]],
        tipo="bar",
        eje_x="segmento",
        eje_y="clientes",
    )
    return json.dumps(
        {
            "png_base64": base64.b64encode(png["png"]).decode("ascii"),
            "especificacion": png["especificacion"],
            "columnas": ["segmento", "clientes"],
            "filas": [["Banca masiva", 10], ["Banca preferente", 5]],
        }
    )


def test_nodo_data_analyst_agrega_grafica_si_el_usuario_la_pide(monkeypatch):
    llamadas = []

    def fake_mcp(servidor, herramienta, argumentos):
        llamadas.append((servidor, herramienta, argumentos))
        if herramienta == "analizar_datos":
            return _respuesta_con_sql()
        if herramienta == "graficar_consulta":
            return _salida_grafica()
        raise AssertionError(herramienta)

    monkeypatch.setattr(grafo, "_llamar_mcp", fake_mcp)
    estado = {
        "consulta": "Haz una gráfica de clientes por segmento",
        "perfil": {"rol": "superusuario"},
        "contexto_analista": "",
        "ultimo_agente": None,
        "resultados": {},
    }

    resultado = grafo._nodo_data_analyst(estado)

    analista = resultado["resultados"]["data_analyst"]
    assert analista["grafica"].startswith(b"\x89PNG")
    assert analista["sql"].startswith("SELECT segmento")
    assert analista["datos"]["columnas"] == ["segmento", "clientes"]
    assert [traza["name"] for traza in resultado["trazas"]] == [
        "mcp_analizar_datos",
        "mcp_graficar_consulta",
    ]
    assert llamadas[1][2]["perfil"] == {"rol": "superusuario"}


def test_nodo_data_analyst_no_grafica_si_no_se_pide(monkeypatch):
    llamadas = []

    def fake_mcp(servidor, herramienta, argumentos):
        llamadas.append((servidor, herramienta, argumentos))
        return _respuesta_con_sql()

    monkeypatch.setattr(grafo, "_llamar_mcp", fake_mcp)
    estado = {
        "consulta": "Cuenta los clientes por segmento",
        "perfil": {"rol": "superusuario"},
        "contexto_analista": "",
        "ultimo_agente": None,
        "resultados": {},
    }

    resultado = grafo._nodo_data_analyst(estado)

    assert [traza["name"] for traza in resultado["trazas"]] == [
        "mcp_analizar_datos"
    ]
    assert "grafica" not in resultado["resultados"]["data_analyst"]
    assert [herramienta for _, herramienta, _ in llamadas] == ["analizar_datos"]
