from __future__ import annotations

from proyecto.orchestration.grafo import _pistas_plan, planear_rutas


def test_banamex_informativo_va_a_rag():
    assert planear_rutas("¿Qué es Banamex?") == ["normatividad_rag"]


def test_afore_general_no_va_a_rag():
    assert planear_rutas("¿Qué es una afore?") == ["directo"]


def test_afore_banamex_va_a_rag():
    assert planear_rutas("¿Qué es una afore Banamex?") == ["normatividad_rag"]


def test_consulta_analitica_con_banamex_conserva_data_analyst():
    assert planear_rutas("¿Cuántos clientes Banamex hay?") == ["data_analyst"]
    assert planear_rutas("¿Cuál es el saldo de Banamex?") == ["data_analyst"]


def test_aviso_de_privacidad_de_banamex_va_a_rag():
    assert (
        planear_rutas("¿Qué dice el aviso de privacidad de Banamex?")
        == ["normatividad_rag"]
    )


def test_pistas_plan_marcan_banamex_solo_si_no_hay_intencion_analitica():
    informativa = _pistas_plan("¿Qué es Banamex?")
    assert informativa["banamex"] is True
    assert "normatividad_rag" in informativa["dominios"]

    analitica = _pistas_plan("¿Cuántos clientes Banamex hay?")
    assert analitica["banamex"] is True
    assert "normatividad_rag" not in analitica["dominios"]
