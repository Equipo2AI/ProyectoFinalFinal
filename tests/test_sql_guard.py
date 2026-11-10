"""Pruebas deterministas de validación SQL y row-level security."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from proyecto.mcp_servers.data_analyst_backend import (
    COLUMNAS_SENSIBLES,
    TABLAS_EXCLUIDAS,
)
from proyecto.mcp_servers.sql_guard import validar_sql

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine(tmp_path):
    destino = tmp_path / "banco.db"
    conexion = sqlite3.connect(destino)
    try:
        conexion.executescript(
            (RAIZ / "proyecto/bd/crear_bd.sql").read_text(encoding="utf-8")
        )
    finally:
        conexion.close()

    motor = create_engine(f"sqlite:///{destino}")
    try:
        yield motor
    finally:
        motor.dispose()


def _validar(
    engine,
    sql: str,
    perfil: dict | None = None,
    limite_max: int | None = None,
):
    return validar_sql(
        sql,
        engine=engine,
        perfil=perfil,
        tablas_excluidas=TABLAS_EXCLUIDAS,
        columnas_sensibles=COLUMNAS_SENSIBLES,
        limite_max=limite_max,
    )


def test_rechaza_operaciones_de_escritura(engine):
    resultado = _validar(engine, "DELETE FROM dim_cliente")

    assert not resultado.ok
    assert "SELECT" in resultado.errores[0]


def test_rechaza_varias_sentencias(engine):
    resultado = _validar(engine, "SELECT 1; DROP TABLE dim_cliente")

    assert not resultado.ok
    assert "una consulta" in resultado.errores[0]


def test_rechaza_tablas_crudas_y_pii(engine):
    assert not _validar(engine, "SELECT COUNT(*) FROM raw_clientes").ok
    assert not _validar(
        engine,
        "SELECT nombre FROM dim_cliente_persona WHERE num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    ).ok


def test_rechaza_columna_inexistente(engine):
    resultado = _validar(engine, "SELECT columna_falsa FROM dim_cliente")

    assert not resultado.ok
    assert "columna_falsa" in resultado.errores[0]


def test_permite_agregado_sin_perfil_en_modo_demo(engine):
    resultado = _validar(
        engine,
        "SELECT segmento, COUNT(*) AS total FROM vw_cliente_publico "
        "GROUP BY segmento",
    )

    assert resultado.ok
    assert resultado.tablas == ["vw_cliente_publico"]


def test_usuario_requiere_filtro_de_su_cliente(engine):
    sin_filtro = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente",
        {"rol": "usuario", "cliente_id": 1},
    )
    ajeno = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente WHERE num_cte = 2",
        {"rol": "usuario", "cliente_id": 1},
    )
    propio = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente WHERE num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not sin_filtro.ok
    assert not ajeno.ok
    assert propio.ok


def test_usuario_no_puede_evadir_filtro_con_or(engine):
    resultado = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente WHERE num_cte = 1 OR 1 = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok


def test_usuario_puede_filtrar_por_su_cuenta(engine):
    with engine.connect() as conexion:
        propia = conexion.execute(
            text("SELECT num_cta FROM dim_cuenta WHERE num_cte = 1 LIMIT 1")
        ).scalar_one()
        ajena = conexion.execute(
            text("SELECT num_cta FROM dim_cuenta WHERE num_cte <> 1 LIMIT 1")
        ).scalar_one()

    permitida = _validar(
        engine,
        f"SELECT COUNT(*) FROM dim_cuenta WHERE num_cta = {propia}",
        {"rol": "usuario", "cliente_id": 1},
    )
    rechazada = _validar(
        engine,
        f"SELECT COUNT(*) FROM dim_cuenta WHERE num_cta = {ajena}",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert permitida.ok
    assert not rechazada.ok


def test_superusuario_puede_consultar_pii_autorizada(engine):
    resultado = _validar(
        engine,
        "SELECT nombre FROM dim_cliente_persona WHERE num_cte = 1",
        {"rol": "superusuario"},
    )

    assert resultado.ok


def test_usuario_puede_consultar_hechos_solo_con_cuenta_propia(engine):
    with engine.connect() as conexion:
        propia = conexion.execute(
            text("SELECT num_cta FROM dim_cuenta WHERE num_cte = 1 LIMIT 1")
        ).scalar_one()
        ajena = conexion.execute(
            text("SELECT num_cta FROM dim_cuenta WHERE num_cte <> 1 LIMIT 1")
        ).scalar_one()

    sin_filtro = _validar(
        engine,
        "SELECT COUNT(*) FROM fact_saldos_captacoloca_m",
        {"rol": "usuario", "cliente_id": 1},
    )
    permitida = _validar(
        engine,
        "SELECT COUNT(*) FROM fact_saldos_captacoloca_m " f"WHERE num_cta = {propia}",
        {"rol": "usuario", "cliente_id": 1},
    )
    rechazada = _validar(
        engine,
        "SELECT COUNT(*) FROM fact_saldos_captacoloca_m " f"WHERE num_cta = {ajena}",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not sin_filtro.ok
    assert permitida.ok
    assert not rechazada.ok


def test_rechaza_subconsulta_sin_filtro_de_cliente(engine):
    resultado = _validar(
        engine,
        "SELECT (SELECT COUNT(*) FROM dim_cliente) "
        "FROM dim_cliente WHERE num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok


def test_rechaza_self_join_sin_igualdad_de_cliente(engine):
    resultado = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente c "
        "JOIN dim_cliente d ON 1 = 1 WHERE c.num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok


def test_permite_self_join_unido_por_num_cte(engine):
    resultado = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cliente c "
        "JOIN dim_cliente d ON c.num_cte = d.num_cte "
        "WHERE c.num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert resultado.ok


def test_rechaza_cte_sin_filtro_de_cliente(engine):
    resultado = _validar(
        engine,
        "WITH total AS (SELECT COUNT(*) AS n FROM dim_cliente) " "SELECT n FROM total",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert not resultado.ok


def test_permite_join_de_cuenta_y_hechos_por_cuenta(engine):
    resultado = _validar(
        engine,
        "SELECT COUNT(*) FROM dim_cuenta d "
        "JOIN fact_saldos_captacoloca_m f ON d.num_cta = f.num_cta "
        "WHERE d.num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
    )

    assert resultado.ok


def test_agrega_limite_si_falta(engine):
    resultado = _validar(
        engine,
        "SELECT * FROM dim_cliente WHERE num_cte = 1",
        {"rol": "usuario", "cliente_id": 1},
        limite_max=51,
    )

    assert resultado.ok
    assert resultado.requiere_limite is True
    assert resultado.limite_aplicado == 51
    assert resultado.normalizado.endswith("LIMIT 51")
