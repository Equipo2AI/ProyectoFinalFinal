"""US-04 · Visualizar resultados analíticos (P1)."""

from __future__ import annotations

import base64
import json

import pytest

from proyecto.charts import generar_grafica

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def clientes_por_segmento(consultar):
    filas = consultar(
        "SELECT s.descripcion AS segmento, COUNT(*) AS clientes "
        "FROM dim_cliente c JOIN dim_segmento s USING (cve_segmento) "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    return ["segmento", "clientes"], [list(f) for f in filas]


def test_ca01_la_grafica_usa_las_filas_de_la_consulta(clientes_por_segmento):
    columnas, filas = clientes_por_segmento
    grafica = generar_grafica(columnas, filas)
    assert grafica["png"].startswith(PNG)
    especificacion = grafica["especificacion"]
    assert especificacion["filas"] == len(filas) == 3
    assert especificacion["categorias"] == 3


def test_ca02_titulo_y_ejes(clientes_por_segmento):
    columnas, filas = clientes_por_segmento
    especificacion = generar_grafica(columnas, filas)["especificacion"]
    assert especificacion["eje_x"] == "segmento"
    assert especificacion["eje_y"] == "clientes"
    assert especificacion["titulo"] == "clientes por segmento"

    titulada = generar_grafica(columnas, filas, titulo="Clientes por segmento (número)")
    assert titulada["especificacion"]["titulo"] == "Clientes por segmento (número)"


@pytest.mark.parametrize(
    ("columnas", "filas"),
    [
        (["total_clientes"], [[5000]]),         # un escalar: no hay eje de categorías
        (["segmento", "clientes"], []),          # sin filas
        (["num_cte", "saldo"], [[1, 10.0], [2, 20.0]]),  # identificador como eje
    ],
)
def test_ca03_datos_no_graficables_se_rechazan_con_mensaje(columnas, filas):
    with pytest.raises(ValueError) as error:
        generar_grafica(columnas, filas)
    assert str(error.value)  # mensaje legible para la alerta de la UI


def test_ca03_el_servicio_conserva_la_respuesta_si_falla_la_grafica():
    from proyecto.servicio import _grafica_de_salida

    png, especificacion, tabla = _grafica_de_salida(json.dumps({"error": "No hay filas para graficar."}))
    assert (png, especificacion, tabla) == (None, {}, None)

    salida = {
        "png_base64": base64.b64encode(PNG + b"resto").decode(),
        "columnas": ["segmento", "clientes"],
        "filas": [["Banca masiva", 3750]],
        "especificacion": {"titulo": "clientes por segmento"},
    }
    png, especificacion, tabla = _grafica_de_salida(json.dumps(salida))
    assert png.startswith(PNG)
    assert tabla == [{"segmento": "Banca masiva", "clientes": 3750}]  # misma fuente que la tabla
