"""Tests del estilo de respuesta por tipo de usuario (sin Ollama ni login)."""

from proyecto.orchestration.perfil_respuesta import (
    SIMPLE,
    TECNICO,
    PerfilRespuesta,
    cifras_conservadas,
    debe_redactar,
    detectar_perfil,
    quitar_sql,
    redactar_respuesta,
)

RESPUESTA_ANALISTA = (
    "Hay 1,250 clientes activos en 2026.\n\n"
    "SQL ejecutado:\n```sql\nSELECT COUNT(*) FROM dim_cliente_persona\n```"
)


# ---------------------------- detección -------------------------------------

def test_rol_normal_sin_pistas_es_simple():
    perfil = detectar_perfil("normal", "¿Cuánto tengo en mi cuenta?")
    assert perfil == PerfilRespuesta(SIMPLE, False, "rol")


def test_rol_auditor_sin_pistas_es_tecnico():
    perfil = detectar_perfil("auditor", "¿Cuántos clientes activos hay?")
    assert perfil == PerfilRespuesta(TECNICO, False, "rol")


def test_normal_que_pide_justificacion_sube_a_tecnico():
    perfil = detectar_perfil("normal", "Justifica la cifra con su metodología")
    assert perfil.estilo == TECNICO and perfil.origen == "consulta"


def test_auditor_que_pide_simple_baja_a_simple():
    perfil = detectar_perfil("auditor", "Explícamelo más sencillo por favor")
    assert perfil.estilo == SIMPLE and perfil.origen == "consulta"


def test_acentos_y_mayusculas_no_afectan():
    assert detectar_perfil("normal", "METODOLOGÍA").estilo == TECNICO


def test_sql_solo_se_muestra_si_auditor_lo_pide():
    assert detectar_perfil("auditor", "dame el SQL").mostrar_sql is True
    assert detectar_perfil("auditor", "cuántos clientes hay").mostrar_sql is False
    # Un usuario normal no ve SQL aunque lo pida (no tiene permiso governance).
    assert detectar_perfil("normal", "dame el SQL").mostrar_sql is False


def test_rol_desconocido_es_simple():
    assert detectar_perfil("otro", "hola").estilo == SIMPLE


# ---------------------------- utilidades ------------------------------------

def test_quitar_sql_elimina_bloque_y_encabezado():
    limpio = quitar_sql(RESPUESTA_ANALISTA)
    assert "SELECT" not in limpio and "SQL ejecutado" not in limpio
    assert "1,250 clientes" in limpio


def test_cifras_conservadas_detecta_cifra_inventada():
    assert cifras_conservadas("Hay 1,250 clientes", "Son 1250 clientes")
    assert not cifras_conservadas("Hay 1,250 clientes", "Son 1,300 clientes")


def test_cifras_conservadas_ignora_listas_numeradas():
    assert cifras_conservadas("Hay 1,250 clientes", "1. Hay 1,250 clientes\n2. Todo bien")


TABLA_CUENTAS = """**Tus cuentas contratadas**
| Cuenta | Producto |
|---|---|
| 100000 | Cuenta de Cheques |
| 200000 | Cuenta de Ahorro |
| 300000 | Tarjeta de Crédito |
| 400000 | Crédito Personal |
Fuente: SQLite, dentro del alcance de la identidad autenticada.
SQL ejecutado (solo lectura):
```sql
SELECT num_cta FROM dim_cuenta
```"""


def test_conteo_de_filas_de_tabla_es_valido():
    # Caso real: "¿cuántas cuentas tengo?" -> el 4 sale de contar filas.
    assert cifras_conservadas(TABLA_CUENTAS, "Tienes 4 cuentas: cheques, ahorro...")
    assert not cifras_conservadas(TABLA_CUENTAS, "Tienes 5 cuentas.")


def test_conteo_de_lista_es_valido():
    assert cifras_conservadas("- a\n- b\n- c\nfin", "Son 3 elementos")


def test_quitar_sql_elimina_encabezado_con_parentesis():
    limpio = quitar_sql(TABLA_CUENTAS)
    assert "SQL ejecutado" not in limpio and "SELECT" not in limpio


def test_no_se_redacta_ocr():
    trazas = [{"name": "mcp_extraer_texto_imagen"}]
    assert not debe_redactar("texto", trazas, {})


def test_no_se_redacta_si_todo_fallo():
    assert not debe_redactar("error", [], {"data_analyst": {"estado": "error"}})


# ---------------------------- redacción -------------------------------------

def _modelo_fijo(texto):
    return lambda modelo, mensajes: texto


def test_redaccion_valida_se_usa_y_sin_sql():
    perfil = PerfilRespuesta(SIMPLE, False, "rol")
    nueva = redactar_respuesta(
        "¿cuántos clientes?", RESPUESTA_ANALISTA, perfil, {}, "m",
        llamar_modelo=_modelo_fijo("Tienes 1,250 clientes activos este año."),
    )
    assert nueva == "Tienes 1,250 clientes activos este año."


def test_redaccion_con_cifra_inventada_se_descarta():
    perfil = PerfilRespuesta(SIMPLE, False, "rol")
    nueva = redactar_respuesta(
        "¿cuántos clientes?", RESPUESTA_ANALISTA, perfil, {}, "m",
        llamar_modelo=_modelo_fijo("Tienes unos 1,300 clientes."),
    )
    # Se usa la respuesta verificada, sin el SQL.
    assert "1,250" in nueva and "SELECT" not in nueva


def test_si_el_modelo_falla_se_devuelve_la_verificada():
    def falla(modelo, mensajes):
        raise RuntimeError("ollama caído")

    perfil = PerfilRespuesta(TECNICO, True, "consulta")
    nueva = redactar_respuesta("sql", RESPUESTA_ANALISTA, perfil, {}, "m", llamar_modelo=falla)
    assert nueva == RESPUESTA_ANALISTA  # el auditor pidió SQL: se conserva


def test_prompt_tecnico_pide_secciones():
    capturado = {}

    def espia(modelo, mensajes):
        capturado["system"] = mensajes[0]["content"]
        return "**Respuesta**: 1,250"

    perfil = PerfilRespuesta(TECNICO, False, "rol")
    redactar_respuesta("x", RESPUESTA_ANALISTA, perfil, {}, "m", llamar_modelo=espia)
    for seccion in ("Fuentes y tablas", "Metodología", "Limitaciones y supuestos"):
        assert seccion in capturado["system"]
    assert "No incluyas el SQL" in capturado["system"]
