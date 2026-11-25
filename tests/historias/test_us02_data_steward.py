"""US-02 · Consultar metadatos y calidad sin exponer registros (P0).

Cifras de referencia: calidad_hallazgos_esperados en proyecto/bd/crear_bd.sql.
"""

from __future__ import annotations

import json

import pytest

from proyecto.agents.data_steward_agent import (
    evaluar_alcance_data_steward,
    evaluar_calidad,
    inspeccionar_dataset,
    validar_calidad_datos,
)


@pytest.fixture
def steward(operador):
    with operador.sesion("data_steward"):
        yield


def _json(herramienta, tabla: str) -> dict:
    return json.loads(herramienta.invoke({"tabla": tabla}))


# ------------------------------------------------------------------ CA-01


def test_ca01_faltantes_y_duplicados_con_cantidades_y_columnas(steward):
    calidad = _json(validar_calidad_datos, "raw_clientes")
    assert calidad["filas"] == 5015
    assert calidad["valores_faltantes_por_columna"]["segmento"] == 40
    assert calidad["valores_faltantes_por_columna"]["cve_segmento"] == 40
    assert calidad["filas_duplicadas_exactas"] == 15


def test_ca01_saldos_nulos_negativos_y_claves_repetidas(steward):
    calidad = _json(validar_calidad_datos, "raw_saldos")
    assert calidad["valores_faltantes_por_columna"] == {"saldo_mes": 30}
    assert calidad["valores_negativos_por_columna"] == {"saldo_mes": 25}
    assert calidad["clave_usada"] == ["num_cta", "cve_fecha_informacion"]
    assert calidad["filas_sobrantes_por_clave"] == 10


def test_ca01_evaluacion_coincide_con_hallazgos_esperados(steward):
    evaluacion = _json(evaluar_calidad, "raw_clientes")
    assert evaluacion["resumen"] == {"esperados": 5, "coinciden": 5, "diferencias": 0}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "raw_clientes no declara PK ni un hallazgo por num_cte, así que "
        "validar_calidad_datos no cuenta IDs repetidos (clave_usada=[]); "
        "solo reporta filas duplicadas exactas."
    ),
)
def test_ca01_ids_de_cliente_repetidos_se_cuentan(steward):
    calidad = _json(validar_calidad_datos, "raw_clientes")
    assert calidad["clave_usada"] == ["num_cte"]
    assert calidad["filas_sobrantes_por_clave"] == 15


# ------------------------------------------------------------------ CA-02


def test_ca02_estructura_con_columnas_tipos_y_filas(steward):
    estructura = _json(inspeccionar_dataset, "raw_saldos")
    assert estructura["filas"] == 20013
    columnas = {c["nombre"]: c["tipo"] for c in estructura["columnas"]}
    assert columnas == {
        "num_cta": "INTEGER",
        "cve_producto": "TEXT",
        "cve_fecha_informacion": "INTEGER",
        "saldo_mes": "REAL",
    }
    assert "raw_saldos" in estructura["catalogo_tablas"]


def test_ca02_clasifica_columnas_pii_por_esquema(steward):
    estructura = _json(inspeccionar_dataset, "raw_clientes")
    assert {"nombre", "correo", "telefono", "rfc", "curp"} <= set(estructura["columnas_pii"])


# ------------------------------------------------------------------ CA-03


def test_ca03_las_salidas_no_contienen_valores_personales(steward, consultar):
    muestra = consultar(
        "SELECT nombre, correo, telefono, rfc, curp FROM dim_cliente_persona LIMIT 200"
    )
    valores = {str(v) for fila in muestra for v in fila if v and len(str(v)) >= 6}
    salidas = " ".join(
        herramienta.invoke({"tabla": tabla})
        for herramienta in (inspeccionar_dataset, validar_calidad_datos, evaluar_calidad)
        for tabla in ("raw_clientes", "raw_saldos")
    )
    filtrados = [v for v in valores if v in salidas]
    assert filtrados == []


def test_ca03_la_tabla_de_personas_no_esta_disponible(steward):
    salida = _json(inspeccionar_dataset, "dim_cliente_persona")
    assert "no existe o no está disponible" in salida["error"]


def test_ca03_solicitud_de_registros_personales_se_rechaza():
    alcance = evaluar_alcance_data_steward(
        "Muéstrame los correos de los clientes con correo inválido en raw_clientes"
    )
    assert alcance["estado"] == "fuera_de_dominio"
    assert alcance["destino"] == "privacidad"


# ------------------------------------------------------------------ CA-04


@pytest.mark.parametrize("herramienta", [inspeccionar_dataset, validar_calidad_datos, evaluar_calidad])
def test_ca04_tabla_inexistente_se_declara(steward, herramienta):
    salida = _json(herramienta, "raw_prestamos")
    assert salida["error"] == "La tabla 'raw_prestamos' no existe o no está disponible."
    assert "columnas" not in salida and "filas" not in salida
    assert "raw_clientes" in salida["catalogo_tablas"]  # ofrece lo que sí existe
