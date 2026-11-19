"""US-03 · Responder preguntas cuantitativas con SQL de solo lectura (P0)."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine

from proyecto.agents.database import COLUMNAS_SENSIBLES, get_database_url
from proyecto.guardrails import redactar_pii
from proyecto.mcp_servers.sql_guard import validar_sql
from proyecto.metrics import obtener_metrica
from proyecto.periods import resolver_periodo


@pytest.fixture(scope="module")
def engine(base_sintetica):
    return create_engine(get_database_url())


USUARIO_1 = {"rol": "usuario", "cliente_id": 1}
SUPERUSUARIO = {"rol": "superusuario"}


# ------------------------------------------------------------------ CA-01


def test_ca01_metrica_certificada_y_periodo():
    metrica = obtener_metrica("numero_clientes")
    assert metrica is not None and metrica.nombre == "clientes"
    assert metrica.agregacion == "count_distinct"
    assert "tipo_persona" in metrica.dimensiones

    periodo = resolver_periodo("2026-06")
    assert (periodo.inicio, periodo.fin) == ("2026-06-01", "2026-06-30")
    assert periodo.anio_mes_inicio == periodo.anio_mes_fin == 202606


def test_ca01_la_metrica_coincide_con_la_base(consultar):
    assert dict(consultar("SELECT cve_tipo_persona, COUNT(*) FROM dim_cliente GROUP BY 1")) == {
        "PF": 4800,
        "PM": 200,
    }


# ------------------------------------------------------------------ CA-02 / CA-03


def test_ca02_select_valido_se_acepta(engine):
    resultado = validar_sql(
        "SELECT cve_producto, COUNT(*) AS cuentas FROM dim_cuenta GROUP BY cve_producto",
        engine=engine,
        perfil=SUPERUSUARIO,
    )
    assert resultado.ok, resultado.errores


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_cliente WHERE num_cte = 1",
        "UPDATE dim_cliente SET cve_segmento = 'X'",
        "INSERT INTO dim_segmento VALUES ('X', 'Y')",
        "DROP TABLE dim_cliente",
        "ATTACH DATABASE 'otra.db' AS otra",
        "PRAGMA writable_schema = ON",
        "SELECT 1; DELETE FROM dim_cliente",
    ],
)
def test_ca03_escrituras_y_varias_sentencias_se_bloquean(engine, sql):
    resultado = validar_sql(sql, engine=engine, perfil=SUPERUSUARIO)
    assert not resultado.ok
    assert resultado.errores


def test_ca03_se_aplica_el_limite_de_filas(engine):
    resultado = validar_sql(
        "SELECT num_cta, saldo_mes FROM vw_saldo_mensual WHERE anio_mes = 202606",
        engine=engine,
        perfil=SUPERUSUARIO,
        limite_max=50,
    )
    assert resultado.ok and resultado.requiere_limite
    assert resultado.limite_aplicado == 50
    assert resultado.normalizado.rstrip().upper().endswith("LIMIT 50")


def test_ca03_la_proyeccion_por_identidad_es_de_solo_lectura(operador, base_sintetica):
    from proyecto.security.scoped_sqlite import connect_scoped

    with operador.sesion("cliente_1"):
        conexion = connect_scoped(base_sintetica)
    try:
        assert conexion.execute("SELECT COUNT(*) FROM dim_cliente").fetchone()[0] == 1
        cuentas = {fila[0] for fila in conexion.execute("SELECT num_cta FROM dim_cuenta")}
        assert cuentas == {100000, 200000, 300000, 400000}
        with pytest.raises(sqlite3.DatabaseError):
            conexion.execute("DELETE FROM dim_cuenta")
    finally:
        conexion.close()


# ------------------------------------------------------------------ CA-04


def test_ca04_pii_sin_permiso_se_deniega(engine):
    resultado = validar_sql(
        "SELECT rfc FROM dim_cliente_persona WHERE num_cte = 1",
        engine=engine,
        perfil=USUARIO_1,
        columnas_sensibles=COLUMNAS_SENSIBLES,
        tablas_excluidas={"dim_cliente_persona"},
    )
    assert not resultado.ok
    assert resultado.tipo_error == "autorizacion_denegada"


def test_ca04_la_proyeccion_solo_incluye_pii_otorgada(operador, base_sintetica):
    from proyecto.security.scoped_sqlite import connect_scoped

    with operador.sesion("auditor"):  # pii:nombre + pii:rfc, sin correo/teléfono
        conexion = connect_scoped(base_sintetica)
    try:
        columnas = {fila[1] for fila in conexion.execute("PRAGMA table_info(dim_cliente_persona)")}
        assert columnas == {"num_cte", "nombre", "rfc"}
    finally:
        conexion.close()

    with operador.sesion("analista"):  # sin PII
        conexion = connect_scoped(base_sintetica)
    try:
        tablas = {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master")}
        assert "dim_cliente_persona" not in tablas
    finally:
        conexion.close()


def test_ca04_la_respuesta_redacta_pii_evidente():
    texto, tipos = redactar_pii("Contacto: ana.perez@example.com, RFC PEGA850101AB1")
    assert "@" not in texto and "PEGA850101AB1" not in texto
    assert set(tipos) >= {"correo", "rfc"}


def test_ca04_usuario_no_consulta_otro_cliente(engine):
    resultado = validar_sql(
        "SELECT saldo_mes FROM vw_saldo_mensual WHERE num_cte = 25",
        engine=engine,
        perfil=USUARIO_1,
    )
    assert not resultado.ok
    assert resultado.tipo_error == "autorizacion_denegada"
