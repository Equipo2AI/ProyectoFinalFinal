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
import json
import pytest

RESPUESTA_ANALISTA = (
    "Hay 1,250 clientes activos en 2026.\n\n"
    "SQL ejecutado:\n```sql\nSELECT COUNT(*) FROM dim_cliente_persona\n```"
)


@pytest.mark.parametrize('source,quote,answer,mode', [
    ('reglamento.pdf, página 2', 'El acceso a los datos requiere autorización.', 'Solo se puede acceder a los datos con autorización.', 'respuesta'),
    ('manual.pdf, sección Respaldo', 'El operador registra el respaldo y comprueba su recuperación.', 'El procedimiento pide registrar el respaldo y verificar que pueda recuperarse.', 'resumen'),
    ('estado.pdf, página 1', 'Saldo final: 9100.00', 'El saldo al cierre es de 9100.00.', 'detalle'),
])
def test_documental_redaccion_comun_sin_chunks(source, quote, answer, mode):
    from proyecto.orchestration.perfil_respuesta import responder_con_evidencia
    captured = []
    def llm(model, messages):
        captured.extend(messages)
        return json.dumps({'answerable': True, 'mode': mode, 'answer': answer,
                           'quotes': [{'id': 'E1', 'text': quote}]})
    result = responder_con_evidencia('Explica el contenido pertinente',
        [{'id': 'E1', 'source': source, 'text': quote, 'similarity': .98765}], 'mimo-doble', llamar_modelo=llm)
    assert result['status'] == 'respondido' and answer in result['message']
    assert source in result['message'] and 'Referencias:' in result['message']
    assert '0.98765' not in str(captured) and 'similarity' not in str(captured)
    assert result['evidence'][0]['text'] == quote


@pytest.mark.parametrize('payload', [
    {'answerable': True, 'mode': 'respuesta', 'answer': 'El saldo es 9900.00.', 'quotes': [{'id': 'E1', 'text': 'Saldo: 9100.00'}]},
    {'answerable': True, 'mode': 'respuesta', 'answer': 'Dato inventado.', 'quotes': [{'id': 'E1', 'text': 'No está en la fuente'}]},
    {'answerable': True, 'mode': 'respuesta', 'answer': 'Score 0.99', 'quotes': [{'id': 'E1', 'text': 'Saldo: 9100.00'}]},
])
def test_documental_no_publica_respaldo_crudo_ni_inventado(payload):
    from proyecto.orchestration.perfil_respuesta import responder_con_evidencia
    result = responder_con_evidencia('Consulta', [{'id': 'E1', 'source': 'documento.pdf', 'text': 'Saldo: 9100.00'}],
        'doble', llamar_modelo=lambda *_: json.dumps(payload))
    assert result['status'] == 'error_tecnico' and '9100.00' not in result['message']


def test_documental_rag_referencias_sin_duplicar_evidencia():
    from proyecto.orchestration.perfil_respuesta import evidencia_documental_rag
    from proyecto.orchestration.grafo import _agregar_evidencia_normativa
    raw = '## Evidencia normativa recuperada\nFUENTE: politica.pdf, página 3 (score híbrido 0.99)\nTexto reservado para verificación.\n\n---\n\nFUENTE: manual.pdf, página 4\nOtro fragmento.'
    evidence = evidencia_documental_rag(raw)
    assert len(evidence) == 2 and evidence[0]['source'] == 'politica.pdf, página 3'
    text = _agregar_evidencia_normativa('Explicación breve.', {'normatividad_rag': {'respuesta': raw}})
    assert 'política' not in text and 'politica.pdf' in text
    assert 'Texto reservado' not in text and '0.99' not in text
    assert _agregar_evidencia_normativa(text, {'normatividad_rag': {'respuesta': raw}}) == text


@pytest.mark.parametrize('query,topic', [
    ('Explica qué dice el manual sobre respaldos', 'qué dice el manual sobre respaldos'),
    ('Resume el procedimiento de registro', 'el procedimiento de registro'),
    ('¿Cuáles son los controles de acceso?', '¿Cuáles son los controles de acceso?'),
])
def test_documental_estilo_no_se_exige_como_evidencia(query, topic):
    from proyecto.orchestration.perfil_respuesta import consulta_recuperacion_documental
    assert consulta_recuperacion_documental(query) == topic


def test_documental_formato_equivalente_y_referencias_verificadas():
    from proyecto.orchestration.perfil_respuesta import responder_con_evidencia
    evidence = [{'id': 'E1', 'source': 'manual.pdf, página 1', 'text': 'La proteccio\u0301n  requiere\n control de acceso.'}]
    payload = {'answerable': True, 'mode': 'RESPUESTA', 'answer': 'Se necesita control de acceso. [E1]',
               'quotes': [{'id': 'E1', 'text': 'La protección requiere control de acceso.'}]}
    result = responder_con_evidencia('Explica el control', evidence, 'doble', llamar_modelo=lambda *_: json.dumps(payload))
    assert result['status'] == 'respondido' and '[E1]' not in result['message']
    assert 'manual.pdf, página 1' in result['message']
    payload['answer'] = 'Se necesita control de acceso. [E9]'
    assert responder_con_evidencia('Explica el control', evidence, 'doble',
        llamar_modelo=lambda *_: json.dumps(payload))['status'] == 'error_tecnico'


@pytest.mark.parametrize('ids,expected', [(['E1'], 'respondido'), (['E999'], 'error_tecnico'), ([], 'error_tecnico')])
def test_documental_cita_canonica_por_identificador(ids, expected):
    from proyecto.orchestration.perfil_respuesta import responder_con_evidencia
    payload = {'answerable': True, 'mode': 'resumen', 'answer': 'El respaldo debe poder recuperarse.', 'evidence_ids': ids}
    literal = 'El operador debe comprobar la recuperación del respaldo.'
    result = responder_con_evidencia('Resume el procedimiento',
        [{'id': 'E1', 'source': 'manual.pdf', 'text': literal}], 'doble', llamar_modelo=lambda *_: json.dumps(payload))
    assert result['status'] == expected
    if expected == 'respondido':
        assert result['evidence'][0]['text'] == literal and literal not in result['message']


def test_documental_seguimiento_rag_no_busca_etiquetas_de_presentacion():
    from proyecto.orchestration.grafo import _subpregunta_normativa
    state = {'consulta': 'Muéstrame el texto completo de esa sección', 'historial': [
        {'role': 'user', 'content': 'Explica el control de acceso en el manual de seguridad'},
        {'role': 'assistant', 'content': 'Se requiere autorización.\n\nReferencias:\n- manual_seguridad.pdf, página 2'}]}
    query = _subpregunta_normativa(state)
    assert 'control de acceso' in query and 'manual_seguridad.pdf' in query
    assert 'Consulta anterior' not in query and 'Referencias anteriores' not in query
    assert 'Muéstrame' not in query and 'Explica' not in query


def test_documental_error_de_recuperacion_no_se_presenta_como_ausencia():
    from proyecto.orchestration.grafo import _nodo_sintetizar
    result = _nodo_sintetizar({'plan': ['normatividad_rag'], 'resultados': {
        'normatividad_rag': {'estado': 'error', 'respuesta': 'Error técnico interno reservado'}}})
    assert 'fallo técnico' in result['respuesta'] and 'interno reservado' not in result['respuesta']


def test_documental_consulta_mixta_conserva_resultado_sql_verificado(monkeypatch):
    from proyecto.orchestration import grafo
    from proyecto.config import ollama_runtime
    from unittest.mock import Mock
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'respuesta',
        'answer': 'Los datos requieren control de acceso.', 'evidence_ids': ['E1']})
    factory = Mock(return_value=model)
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', factory)
    state = {'consulta': 'Consulta mis datos y explica el control normativo',
             'plan': ['data_analyst', 'normatividad_rag'], 'resultados': {
                'data_analyst': {'estado': 'ok', 'respuesta': 'Resultado SQL verificado sin reformular.', 'respuesta_verificada': True},
                'normatividad_rag': {'estado': 'ok', 'respuesta': 'FUENTE: control.pdf, página 1 (score 0.98)\nLos datos requieren control de acceso.'}}}
    answer = grafo._nodo_sintetizar(state)['respuesta']
    assert 'Resultado SQL verificado sin reformular.' in answer and 'Los datos requieren control de acceso.' in answer
    assert 'FUENTE:' not in answer and '0.98' not in answer and 'control.pdf' in answer
    factory.assert_called_once()


def test_documental_texto_completo_sin_prueba_de_integridad_no_se_inventa():
    from proyecto.orchestration.perfil_respuesta import responder_con_evidencia
    result = responder_con_evidencia('Muéstrame el artículo completo',
        [{'id': 'E1', 'source': 'norma.pdf', 'text': 'Solo un pasaje.'}], 'doble',
        llamar_modelo=lambda *_: json.dumps({'answerable': True, 'mode': 'completo', 'section_id': None}))
    assert result['status'] == 'sin_evidencia' and 'íntegro' in result['message']


@pytest.mark.parametrize('role', ['usuario', 'superusuario'])
@pytest.mark.parametrize('name,quote', [('norma.pdf', 'El acceso requiere autorización.'), ('manual.pdf', 'El respaldo debe verificarse.')])
def test_documental_grafo_no_reanexa_chunks_para_ningun_rol(monkeypatch, role, name, quote):
    from proyecto.orchestration import grafo
    from proyecto.config import ollama_runtime
    from unittest.mock import Mock
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'respuesta',
        'answer': 'La evidencia explica el control solicitado.', 'quotes': [{'id': 'E1', 'text': quote}]})
    factory = Mock(return_value=model)
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', factory)
    state = {'consulta': 'Explica el control', 'plan': ['normatividad_rag'], 'modelo': 'mimo-doble',
             'perfil': {'rol': role}, 'perfil_respuesta': detectar_perfil('auditor' if role == 'superusuario' else 'normal', '').a_dict(),
             'resultados': {'normatividad_rag': {'estado': 'ok', 'respuesta': 'FUENTE: ' + name + ', página 2 (score 0.98)\n' + quote}}}
    generated = grafo._nodo_sintetizar(state)
    state.update(generated)
    final = grafo._nodo_redactar(state).get('respuesta', state['respuesta'])
    assert 'La evidencia explica' in final and name in final
    assert quote not in final and '0.98' not in final and 'FUENTE:' not in final
    factory.assert_called_once()


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


def test_voz_user_contexto_seleccionado_y_respuesta_no_copia_plantilla():
    captured = {}
    facts = ['Puedo orientar sobre Cuenta de prueba. ¿Te interesa esa cuenta?',
             'Puedes solicitar la revisión de tus PDF autorizados.']
    context = {'producto_confirmado': None, 'producto_propuesto': 'Cuenta de prueba',
               'interpretacion_turno': {'objetivo': 'apertura', 'referencia_clara': False},
               'requisitos_documentados': {'reglas': [{'antiguedad_maxima_meses': 3}]}}
    candidate = 'Puedo orientarte y revisar tus PDF cuando los adjuntes. ¿Te interesa la Cuenta de prueba?'
    def model(_, messages):
        captured['messages'] = messages
        return candidate
    diagnostic = {}
    result = redactar_respuesta('Quiero empezar', '\n'.join(facts), PerfilRespuesta(SIMPLE, False, 'rol'),
        {}, 'doble', llamar_modelo=model, hechos_protegidos=facts, contexto=context,
        historial=[{'role': 'user', 'content': 'Pregunta anterior'}], diagnostico=diagnostic)
    assert result == candidate and diagnostic == {'origen': 'modelo', 'motivo': None}
    system = captured['messages'][0]['content']
    assert 'asistente bancario que conversa' in system
    assert 'no una plantilla' in system
    assert 'Reescríbela' not in system and 'Entre 2 y 5 frases' not in system
    assert 'antiguedad_maxima_meses' not in system
    assert 'producto_propuesto' in system
    assert captured['messages'][1]['content'] == 'Pregunta anterior'
    assert captured['messages'][-1]['content'] == 'Quiero empezar'


@pytest.mark.parametrize('candidate', [
    'Necesitas un comprobante de máximo 3 meses para esa cuenta.',
    'Tu cuenta está aprobada.',
])
def test_voz_user_no_relaja_guardas_antes_de_confirmar(candidate):
    facts = ['Puedo orientar sobre Cuenta de prueba. ¿Te interesa esa cuenta?']
    diagnostic = {}
    result = redactar_respuesta('Quiero una cuenta', facts[0], PerfilRespuesta(SIMPLE, False, 'rol'), {},
        'doble', llamar_modelo=lambda *_: candidate, hechos_protegidos=facts, diagnostico=diagnostic)
    assert result == facts[0] and diagnostic['origen'] == 'respaldo'


@pytest.mark.parametrize('candidate,allowed', [
    ('¿Tienes una identificación para que la revisemos?', True),
    ('Debes presentar tu identificación.', False),
    ('Puedo revisar tu identificación y certificar su autenticidad.', False),
    ('Puedo revisar tu comprobante de ingresos.', False),
])
def test_voz_capacidad_de_revision_no_es_requisito(candidate, allowed):
    from proyecto.orchestration.perfil_respuesta import _redaccion_documental_coherente
    assert _redaccion_documental_coherente(candidate, 'Puedo revisar tus PDF autorizados.',
        {'capacidades_documentales': ['ine', 'curp', 'domicilio']}) is allowed


@pytest.mark.parametrize('candidate', [
    'Debes presentar CURP.',
    'Debes presentar RFC.',
    'La fecha es 2030-01-11 y el importe 500.00.',
    'La fecha es 2030-01-10 y el importe 501.00.',
])
def test_voz_user_conserva_condiciones_fechas_e_importes(candidate):
    facts = ['CURP: condicional si no figura en la INE. RFC: si tienes actividad empresarial.',
             'La fecha es 2030-01-10 y el importe 500.00.']
    diagnostic = {}
    result = redactar_respuesta('Aclara el requisito', '\n'.join(facts), PerfilRespuesta(SIMPLE, False, 'rol'), {},
        'doble', llamar_modelo=lambda *_: candidate, hechos_protegidos=facts, diagnostico=diagnostic)
    assert result == '\n'.join(facts) and diagnostic['origen'] == 'respaldo'


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
