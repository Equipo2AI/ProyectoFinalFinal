"""Pruebas de la generación determinista de gráficas."""

from __future__ import annotations

import pytest

from proyecto.charts import MAX_CATEGORIAS, generar_grafica


def _png_es_valido(png: bytes) -> bool:
    return png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000


def test_barra_genera_png_y_especificacion():
    resultado = generar_grafica(
        ["segmento", "clientes"],
        [
            ["Banca masiva", 3750],
            ["Banca preferente", 1000],
            ["Banca patrimonial", 250],
        ],
        tipo="bar",
        eje_x="segmento",
        eje_y="clientes",
        titulo="Clientes por segmento",
        orden="desc",
    )
    assert _png_es_valido(resultado["png"])
    assert resultado["especificacion"]["tipo"] == "bar"
    assert resultado["especificacion"]["eje_x"] == "segmento"
    assert resultado["especificacion"]["eje_y"] == "clientes"
    assert resultado["especificacion"]["filas"] == 3
    assert resultado["advertencias"] == []


def test_auto_elige_barras_para_categorias():
    resultado = generar_grafica(
        ["producto", "total"],
        [["AHORRO", 10], ["TDC", 20], ["CHEQUES", 30]],
    )
    assert resultado["especificacion"]["tipo"] == "bar"
    assert _png_es_valido(resultado["png"])
    assert any("automáticamente" in aviso for aviso in resultado["advertencias"])


def test_auto_elige_scatter_si_ambos_ejes_son_numericos():
    resultado = generar_grafica(
        ["x", "y"],
        [[1, 2], [2, 4], [3, 3]],
    )
    assert resultado["especificacion"]["tipo"] == "scatter"


def test_auto_elige_linea_para_fechas_ordenadas():
    resultado = generar_grafica(
        ["mes", "saldo"],
        [[f"2025-{mes:02d}", mes * 100] for mes in range(1, 7)],
    )
    assert resultado["especificacion"]["tipo"] == "line"
    assert resultado["especificacion"]["eje_x"] == "mes"
    assert resultado["especificacion"]["eje_y"] == "saldo"


def test_auto_elige_linea_para_anio_mes():
    resultado = generar_grafica(
        ["anio_mes", "saldo"],
        [[202601, 100], [202602, 120], [202603, 90]],
    )
    assert resultado["especificacion"]["tipo"] == "line"


def test_scatter_con_dos_columnas_numericas():
    resultado = generar_grafica(
        ["x", "y"],
        [[1, 2], [2, 4], [3, 3]],
        tipo="scatter",
        eje_x="x",
        eje_y="y",
    )
    assert resultado["especificacion"]["tipo"] == "scatter"
    assert _png_es_valido(resultado["png"])


@pytest.mark.parametrize("columna", ["num_cte", "num_cta", "curp", "telefono"])
def test_rechaza_identificadores_y_pii(columna):
    with pytest.raises(ValueError, match="identificador"):
        generar_grafica(
            [columna, "total"],
            [[1, 10], [2, 20]],
            tipo="bar",
            eje_x=columna,
            eje_y="total",
        )


def test_rechaza_y_no_numerico():
    with pytest.raises(ValueError, match="columna numérica"):
        generar_grafica(
            ["segmento", "etiqueta"],
            [["A", "uno"], ["B", "dos"]],
            tipo="bar",
            eje_x="segmento",
            eje_y="etiqueta",
        )


def test_rechaza_categorias_repetidas():
    with pytest.raises(ValueError, match="repetidos"):
        generar_grafica(
            ["segmento", "total"],
            [["A", 10], ["A", 20], ["B", 5]],
            tipo="bar",
            eje_x="segmento",
            eje_y="total",
        )


def test_rechaza_demasiadas_categorias():
    filas = [[f"categoria-{indice}", indice] for indice in range(MAX_CATEGORIAS + 1)]
    with pytest.raises(ValueError, match="categorías"):
        generar_grafica(
            ["categoria", "total"],
            filas,
            tipo="bar",
            eje_x="categoria",
            eje_y="total",
        )


def test_pie_tiene_limite_mas_estricto():
    filas = [[f"categoria-{indice}", indice + 1] for indice in range(11)]
    with pytest.raises(ValueError, match="pastel"):
        generar_grafica(
            ["categoria", "total"],
            filas,
            tipo="pie",
            eje_x="categoria",
            eje_y="total",
        )


def test_rechaza_sin_filas():
    with pytest.raises(ValueError, match="No hay filas"):
        generar_grafica(["x", "y"], [], tipo="bar")


def test_rechaza_tipo_desconocido():
    with pytest.raises(ValueError, match="no soportado"):
        generar_grafica(
            ["x", "y"],
            [["A", 1]],
            tipo="mapa",
            eje_x="x",
            eje_y="y",
        )


def test_omite_filas_invalidas_y_avisa():
    resultado = generar_grafica(
        ["segmento", "total"],
        [["A", 10], ["B", "no disponible"], ["C", 5]],
        tipo="bar",
        eje_x="segmento",
        eje_y="total",
    )
    assert resultado["especificacion"]["filas"] == 2
    assert any("omitieron 1 filas" in aviso for aviso in resultado["advertencias"])
