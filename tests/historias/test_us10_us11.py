"""US-10 (enrutamiento) y US-11 (alcance del prototipo)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------- US-10


@pytest.fixture(scope="module")
def grafo():
    return pytest.importorskip("proyecto.orchestration.grafo")


@pytest.mark.parametrize(
    ("consulta", "especialista"),
    [
        ("¿Cuántos valores nulos tiene raw_saldos?", "data_steward"),
        ("¿Cuál es el saldo total por producto en junio de 2026?", "data_analyst"),
        ("¿Qué dice la Ley Federal de Protección de Datos Personales sobre el consentimiento?", "normatividad_rag"),
        ("Extrae el texto de la imagen factura.png", "document_intelligence"),
    ],
)
def test_us10_ca01_cada_dominio_va_a_su_especialista(grafo, consulta, especialista):
    assert especialista in grafo.planear_rutas(consulta)


def test_us10_ca04_componente_mcp_caido_no_finge_respuesta():
    from proyecto.orchestration.comun import _llamar_mcp

    salida = _llamar_mcp("servidor_que_no_existe", "herramienta", {})
    assert salida.startswith("Error al llamar servidor_que_no_existe.herramienta")


# ------------------------------------------------------------- US-11


def test_us11_ca01_la_documentacion_declara_datos_sinteticos():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8").casefold()
    assert "base sintética" in readme
    assert "beta" in readme or "prototipo" in readme


def test_us11_ca03_se_documentan_los_componentes_remotos():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Pinecone" in readme
    assert "No se indexan en Pinecone" in readme  # documentos del usuario
    entorno = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "LANGSMITH_TRACING=false" in entorno  # trazado externo apagado por defecto


def _hash(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def test_us11_ca04_las_herramientas_no_modifican_la_base(operador, base_sintetica):
    from proyecto.agents.data_steward_agent import evaluar_calidad, validar_calidad_datos
    from proyecto.security.scoped_sqlite import connect_scoped

    antes = _hash(base_sintetica)
    with operador.sesion("data_steward"):
        for tabla in ("raw_clientes", "raw_saldos"):
            json.loads(validar_calidad_datos.invoke({"tabla": tabla}))
            json.loads(evaluar_calidad.invoke({"tabla": tabla}))
    with operador.sesion("auditor"):
        conexion = connect_scoped(base_sintetica)
        conexion.close()
    assert _hash(base_sintetica) == antes


def test_us11_ca04_el_engine_compartido_es_de_solo_lectura(base_sintetica):
    from sqlalchemy.exc import OperationalError

    from proyecto.agents.database import create_readonly_engine, get_database_url

    engine = create_readonly_engine(get_database_url())
    with engine.connect() as conexion, pytest.raises(OperationalError):
        conexion.exec_driver_sql("DELETE FROM dim_segmento")
