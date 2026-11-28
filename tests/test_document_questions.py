"""Solo regresiones nuevas de preguntas sobre adjuntos; sin repetir admision."""
import copy
import json
import secrets
import os
from unittest.mock import Mock

import pytest

from proyecto.agents import document_questions as dq


INTEGRITY_STATEMENT = '''Estado de cuenta sintético
Saldo inicial: 1000.10
Total de abonos: 200.20
Total de cargos: 50.05
Saldo final: 1150.25
Inicio del periodo: 2020-01-01
Fin del periodo: 2020-01-31
'''


def _pertinence_document(document, text):
    result = copy.deepcopy(document)
    result['pages'] = [{'source': 'Página 1', 'text': text}]
    result['fragments'][0]['text'] = text
    result['integrity'] = dq.validate_integrity(result['pages'])
    result['metadata'] = dq.document_metadata(result['pages'])
    return result


def test_pertinencia_debito_historico_calculo_verificable(document, monkeypatch):
    text = INTEGRITY_STATEMENT.replace('Estado de cuenta sintético', 'Estado de cuenta de débito').replace('2020-', '2025-')
    text += 'Período: enero de 2025\nFecha | Concepto | Cargo | Abono\n03/01 | Servicio | 50.05 | 0.00'
    doc = _pertinence_document(document, text)
    from proyecto.config import ollama_runtime
    factory = Mock(side_effect=AssertionError('El cálculo no requiere LLM'))
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', factory)
    result = dq.answer('¿Cuánto gasté ese mes?', doc)
    assert result['code'] == 'OK' and result['calculation']['result'] == '50.05'
    assert result['compatibility']['purpose']['finalidad'] == 'historica'
    assert result['compatibility']['metadata']['type'] == 'debito'
    factory.assert_not_called()


@pytest.mark.parametrize('text,question', [
    ('Estado de cuenta de crédito\nPeríodo: enero 2025\nSaldo final: 450.00\nFecha de corte: 2025-01-31', '¿Cuánto debo pagar actualmente?'),
    ('Reglamento bancario\nVersión: 2023\nFecha de emisión: 2023-01-01\nArtículo 1. Conservar evidencia.', '¿Esta norma sigue vigente hoy?'),
    ('Política de Data Governance\nVersión: 1.0\nSección 1. Aplicación institucional.', '¿Esta política está vigente hoy?'),
    ('Documento bancario sin fecha\nInformación de operaciones de prueba no fechadas.', '¿Cuál es el saldo actual?'),
])
def test_pertinencia_actual_no_infiere_ni_recupera(document, monkeypatch, text, question):
    doc = _pertinence_document(document, text)
    embed = Mock(side_effect=AssertionError('No recuperar sin pertinencia temporal'))
    monkeypatch.setattr(dq, 'embed', embed)
    from proyecto.config import ollama_runtime
    factory = Mock(side_effect=AssertionError('No debe usar LLM'))
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', factory)
    result = dq.answer(question, doc)
    assert result['code'] == 'SIN_EVIDENCIA'
    assert result['compatibility']['purpose']['finalidad'] == 'actual'
    assert '450.00' not in result['message']
    embed.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize('text,question,quote,kind', [
    ('Reglamento bancario\nVersión: 2023\nFecha de emisión: 2023-01-01\nArtículo 1. Conservar evidencia de las operaciones.',
     '¿Qué establecía esta versión sobre evidencia?', 'Artículo 1. Conservar evidencia de las operaciones.', 'normativa_reglamento'),
    ('Política de Data Governance\nVersión: 1.0\nÁmbito de aplicación: área de datos\nSección 2. Los datos sensibles requieren control de acceso.',
     '¿Qué establece sobre datos sensibles?', 'Sección 2. Los datos sensibles requieren control de acceso.', 'politica_gobierno_datos'),
    ('Comprobante bancario\nLa transferencia aparece registrada con referencia de operación sintética.',
     '¿Qué aparece sobre la transferencia?', 'La transferencia aparece registrada con referencia de operación sintética.', 'otro_compatible'),
])
def test_pertinencia_citas_sin_perfil_completo(document, monkeypatch, text, question, quote, kind):
    doc = _pertinence_document(document, text)
    from proyecto.config import ollama_runtime
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'respuesta', 'answer': quote,
                                                  'quotes': [{'id': 'F1', 'text': quote}]})
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', Mock(return_value=model))
    result = dq.answer(question, doc)
    assert result['code'] == 'OK' and quote in result['message']
    assert result['compatibility']['metadata']['type'] == kind
    assert not doc['integrity']['calculations']
    assert doc['integrity']['state'] == 'NO_EVALUABLE'
    assert 'Página 1' in result['message']


def test_pertinencia_ambigua_y_version_no_disponible(document, monkeypatch):
    text = 'Estado de cuenta de crédito\nPeríodo: enero 2025\nSaldo final: 450.00\nFecha de corte: 2025-01-31'
    doc = _pertinence_document(document, text)
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('No recuperar antes de aclarar')))
    assert dq.answer('¿Cuánto debo pagar?', doc)['code'] == 'REQUIERE_ACLARACION'
    norm = _pertinence_document(document, 'Reglamento bancario\nVersión: 2.0\nArtículo 1. Conservar evidencia de operaciones.')
    assert dq.answer('¿Qué establece la versión 3.0?', norm)['code'] == 'SIN_EVIDENCIA'


def test_pertinencia_no_aplica_contabilidad_a_normativa():
    pages = [{'source': 'Página 1', 'text': 'Reglamento bancario\nArtículo 1. El estado de cuenta debe contener saldos, cargos y abonos.'}]
    report = dq.validate_integrity(pages)
    assert not report['calculations']
    assert not any(i['field'] in {'saldo_inicial', 'saldo_final', 'cargos', 'abonos'} for i in report['issues'])


def test_pertinencia_periodo_y_seguimiento_no_cambian_evidencia(document, monkeypatch):
    doc = _pertinence_document(document, INTEGRITY_STATEMENT.replace('2020-', '2025-').replace('sintético', 'de débito'))
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('Período ajeno no recupera')))
    assert dq.answer('¿Cuánto suman los cargos de febrero de 2025?', doc)['code'] == 'SIN_EVIDENCIA'
    assert dq.answer('¿Cuánto suman los cargos de 2025?', doc)['code'] == 'SIN_EVIDENCIA'  # enero no acredita todo el año
    assert dq.answer('¿Y cuánto debo pagar?', doc, '¿Cuánto debo pagar actualmente?')['code'] == 'SIN_EVIDENCIA'


def test_pertinencia_autorizacion_precede_a_metadatos(document, monkeypatch):
    from proyecto.security import document_access as access
    monkeypatch.setattr(access, 'authorize_context', Mock(side_effect=PermissionError('denegado simulado')))
    metadata = Mock(side_effect=AssertionError('No leer contenido denegado'))
    monkeypatch.setattr(dq, 'document_metadata', metadata)
    assert dq.answer('¿Esta norma sigue vigente hoy?', document)['code'] == 'ACCESO_DENEGADO'
    metadata.assert_not_called()


def test_pertinencia_rutas_del_adjunto_y_fuentes_externas():
    from proyecto.orchestration.grafo import interpretar_finalidad_documental
    assert not dq.external_request('¿Esta norma sigue vigente hoy?')
    assert not dq.external_request('¿Qué establecía esta versión?')
    assert dq.external_request('Consulta la normativa oficial vigente en Pinecone')
    assert dq.external_request('¿Qué tablas hay en SQLite?')
    assert interpretar_finalidad_documental('¿Cumple las reglas de calidad?')['calidad']


def test_pertinencia_credito_no_usa_formula_de_debito():
    text = INTEGRITY_STATEMENT.replace('sintético', 'de crédito') + 'Fecha de corte: 2025-01-31\nFecha límite de pago: 2025-01-20'
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}])
    assert not report['calculations']
    assert any(i['field'] == 'fechas' and i['state'] == 'INCONSISTENTE' for i in report['issues'])


@pytest.mark.parametrize('kind,question', [
    ('Reglamento bancario', '¿Cuánto gasté ese mes?'),
    ('Estado de cuenta de débito', '¿Cuánto debo pagar?'),
])
def test_pertinencia_no_confunde_finalidad_del_tipo(document, monkeypatch, kind, question):
    doc = _pertinence_document(document, kind + '\nPeríodo: enero 2025\nSaldo final: 500.00\nArtículo 1. Conservar datos.')
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('Tipo incompatible no recupera')))
    assert dq.answer(question, doc)['code'] == 'SIN_EVIDENCIA'


def test_pertinencia_periodos_contradictorios_y_fuente_no_actual(document, monkeypatch):
    text = INTEGRITY_STATEMENT.replace('2020-', '2025-') + 'Período: febrero 2025'
    doc = _pertinence_document(document, text)
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('Metadatos contradictorios no recuperan')))
    assert dq.answer('¿Cuánto gasté en enero de 2025?', doc)['code'] == 'SIN_EVIDENCIA'
    norm = _pertinence_document(document, 'Normativa bancaria\nVersión: 2023\nVigente hasta: 2099-12-31\nArtículo 1. Conservar evidencia de operaciones.')
    assert dq.answer('¿Esta norma sigue vigente hoy?', norm)['code'] == 'SIN_EVIDENCIA'
    assert dq.answer('¿Es la versión más reciente?', norm)['code'] == 'SIN_EVIDENCIA'


@pytest.mark.parametrize('question', ['¿Cuánto gasté en enero de 2025 y febrero de 2025?', 'Compara la versión 1.0 y la versión 2.0'])
def test_pertinencia_no_responde_solo_el_primer_periodo_o_version(document, monkeypatch, question):
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('Requiere aclaracion antes de recuperar')))
    assert dq.answer(question, document)['code'] == 'REQUIERE_ACLARACION'


@pytest.mark.parametrize('text,incomplete,expected', [
    (INTEGRITY_STATEMENT, False, 'VALIDO'),
    (INTEGRITY_STATEMENT.replace('Saldo inicial: 1000.10\n', ''), False, 'INCOMPLETO'),
    (INTEGRITY_STATEMENT.replace('1150.25', '1150.24'), False, 'INCONSISTENTE'),
    (INTEGRITY_STATEMENT + 'Vigente hasta: 2024-01-01\n', False, 'VENCIDO'),
    (INTEGRITY_STATEMENT.replace('2020-', '2001-'), False, 'VALIDO'),
    (INTEGRITY_STATEMENT.replace('2020-01-01', '2020-02-30'), False, 'INCONSISTENTE'),
    ('Estado de cuenta', False, 'NO_EVALUABLE'),
    (INTEGRITY_STATEMENT, True, 'NO_EVALUABLE'),
])
def test_integridad_casos_sinteticos(text, incomplete, expected):
    from datetime import date
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}], incomplete, today=date(2026, 11, 23))
    assert report['state'] == expected
    assert report['blocking_conclusion'] == (expected != 'VALIDO')
    if expected == 'VALIDO':
        assert report['calculations'][0]['expected'] == '1150.25'
        assert report['calculations'][0]['reported'] == '1150.25'


def test_integridad_varias_incidencias_y_paginas():
    from datetime import date
    text = INTEGRITY_STATEMENT.replace('1150.25', '20.00') + 'Vencimiento: 2024-01-01\nPágina 1 de 2\n'
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}], today=date(2026, 11, 23))
    assert {'INCONSISTENTE', 'VENCIDO', 'INCOMPLETO'} <= set(report['states'])
    assert next(i for i in report['issues'] if i['field'] == 'conciliacion')['page'] == 'Página 1'
    assert report['calculations'][0]['expected'] == '1150.25'


def test_integridad_fechas_contradictorias_e_importes_ambiguos():
    text = INTEGRITY_STATEMENT.replace('2020-01-31', '2019-12-31').replace('1000.10', '1.000,10')
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}])
    assert {'INCONSISTENTE', 'NO_EVALUABLE'} <= set(report['states'])
    assert not report['calculations']


def test_integridad_decimal_no_redondea_importes_largos():
    integer = '1' + '0' * 40
    text = ('Estado de cuenta sintético\nSaldo inicial: ' + integer + '.01\n'
            'Total de abonos: 0.02\nTotal de cargos: 0.01\nSaldo final: ' + integer + '.02')
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}])
    assert report['state'] == 'VALIDO'
    assert report['calculations'][0]['expected'] == integer + '.02'


def test_integridad_extraccion_incompleta_no_afirma_ausencia():
    report = dq.validate_integrity([{'source': 'Página 1', 'text': 'Estado de cuenta parcial: extracción incompleta de prueba.'}], True)
    assert report['state'] == 'NO_EVALUABLE'
    assert not any(i['state'] == 'INCOMPLETO' for i in report['issues'])


def test_integridad_campo_presente_no_extraible_no_es_campo_ausente():
    text = INTEGRITY_STATEMENT.replace('Saldo inicial: 1000.10', '| Saldo inicial | 1000.10 |')
    report = dq.validate_integrity([{'source': 'Página 1', 'text': text}])
    issue = next(i for i in report['issues'] if i['field'] == 'saldo_inicial')
    assert issue['state'] == 'NO_EVALUABLE'
    assert 'mencionado' in issue['message']


def test_integridad_vigencia_se_revisa_al_cambiar_dia(monkeypatch, document):
    document['pages'] = [{'source': 'Página 1', 'text': INTEGRITY_STATEMENT + 'Vencimiento: 2020-01-31'}]
    from datetime import date
    document['integrity'] = dq.validate_integrity(document['pages'], today=date(2020, 1, 1))
    assert document['integrity']['state'] == 'VALIDO'
    monkeypatch.setattr(dq, 'embed', Mock(side_effect=AssertionError('No recuperar para diagnostico')))
    result = dq.answer('¿Está vencido el documento?', document)
    assert result['integrity']['state'] == 'VENCIDO'


def test_integridad_no_inventa_requisitos_ni_perfiles():
    pages = [{'source': 'Sección 1', 'text': 'Política de gobierno de datos. Documento sintético sin perfil legal de campos definido.'}]
    report = dq.validate_integrity(pages)
    assert report['state'] == 'NO_EVALUABLE'
    assert not any(i['state'] == 'INCOMPLETO' for i in report['issues'])
    report = dq.validate_integrity(pages, required_documents=['A', 'B'], supplied_documents=['A'])
    assert [i for i in report['issues'] if i['field'] == 'expediente'][0]['message'].endswith('B.')


def test_integridad_diagnostico_y_bloqueo_sin_modelo(monkeypatch, document):
    document['integrity'] = dq.validate_integrity([{'source': 'Página 1', 'text': INTEGRITY_STATEMENT.replace('1150.25', '20.00')}])
    embed = Mock(side_effect=AssertionError('No debe recuperar para un diagnóstico determinista'))
    monkeypatch.setattr(dq, 'embed', embed)
    from proyecto.config import ollama_runtime
    llm = Mock(side_effect=AssertionError('No debe usar un LLM'))
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', llm)
    assert dq.answer('¿Qué inconsistencias tiene el documento?', document)['status'] == 'diagnostico_limitado'
    assert dq.answer('¿Cuánto suman los cargos?', document)['status'] == 'diagnostico_limitado'
    assert '1150.25' in dq.answer('¿Es correcto el documento?', document)['message']
    embed.assert_not_called()
    llm.assert_not_called()


def test_integridad_dato_literal_sigue_consultable(monkeypatch, document):
    document['integrity'] = dq.validate_integrity(document['pages'])
    from proyecto.config import ollama_runtime
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'quotes': [{'id': 'F1', 'text': 'Saldo final: 9100.00'}]})
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', Mock(return_value=model))
    result = dq.answer('¿Cuál es el saldo final?', document)
    assert result['status'] == 'respondido'
    assert '9100.00' in result['message']


@pytest.fixture
def document(monkeypatch):
    from proyecto.security import document_access as access
    monkeypatch.setattr(access, 'authorize_context', lambda context: None)  # contrato de proposito, no seguridad
    text = "Estado de cuenta de prueba\nSaldo final: 9100.00\nFECHA | CONCEPTO | CARGO | ABONO\n03/09 | Transferencia | 0.00 | 1700.00\n08/09 | Servicio | 900.00 | 0.00"
    monkeypatch.setattr(dq, "model_digest", lambda: "test-digest")
    monkeypatch.setattr(dq, "embed", lambda parts: [[1., 0.] for p in parts])
    return {"model": dq.MODEL, "digest": "test-digest", "incomplete": False, "admission": {"decision": "aceptado"},
            "pages": [{"source": "Página 1", "text": text}],
            "fragments": [{"id": "F1", "source": "Página 1", "text": text, "vector": [1., 0.]}]}


@pytest.mark.parametrize('redacted,incomplete,expected', [(False, False, 'respondido'), (True, False, 'sin_evidencia'), (False, True, 'sin_evidencia')])
def test_documental_seccion_completa_desde_texto_autorizado(document, monkeypatch, redacted, incomplete, expected):
    from proyecto.config import ollama_runtime
    doc = copy.deepcopy(document)
    text = '# Respaldo\nRegistrar el respaldo.\nComprobar la recuperación.\n# Acceso\nExigir autorización.'
    doc['name'] = 'manual_de_prueba.pdf'
    doc['pages'] = [{'source': 'Página 1', 'text': text, 'redacted': redacted}]
    doc['fragments'][0]['text'] = text
    doc['incomplete'] = incomplete
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'completo', 'section_id': 'S1'})
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', Mock(return_value=model))
    result = dq.answer('Muéstrame el texto completo de la sección Respaldo', doc)
    assert result['status'] == expected
    if expected == 'respondido':
        assert '# Respaldo\nRegistrar el respaldo.\nComprobar la recuperación.' in result['message']
        assert '# Acceso' not in result['message'] and result['section_title'] == 'Respaldo'
    else:
        assert 'íntegro' in result['message'] and 'Registrar el respaldo.' not in result['message']


def test_documental_seguimiento_recibe_referencia_anterior(document, monkeypatch):
    from proyecto.config import ollama_runtime
    captured = []
    class Model:
        def __init__(self, **kwargs): pass
        def invoke(self, messages):
            captured.extend(messages)
            return Mock(content=json.dumps({'answerable': True, 'mode': 'detalle',
                'answer': 'El saldo al cierre es de 9100.00.', 'quotes': [{'id': 'F1', 'text': 'Saldo final: 9100.00'}]}))
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', Model)
    result = dq.answer('¿Y qué significa ese saldo?', document, '¿Cuál es el saldo final?')
    assert result['status'] == 'respondido' and '9100.00' in result['message']
    assert '¿Cuál es el saldo final?' in captured[0]['content']
    assert 'Página 1' in result['message']


@pytest.mark.parametrize('text', [
    '# Procedimiento\n## Registro\nRegistrar la operación.\n## Verificación\nVerificar el resultado.\n# Otro tema\nFuera de la sección.',
    'Capítulo I\nArtículo 1. Registrar la operación.\nArtículo 2. Verificar el resultado.\nCapítulo II\nFuera de la sección.',
])
def test_documental_seccion_padre_conserva_subapartados(document, text):
    doc = copy.deepcopy(document)
    doc['pages'] = [{'source': 'Página 1', 'text': text, 'redacted': False}]
    sections = dq._secciones_consultables(doc)
    assert sections[0]['complete'] is True
    assert 'Registrar la operación.' in sections[0]['text'] and 'Verificar el resultado.' in sections[0]['text']
    assert 'Fuera de la sección.' not in sections[0]['text']


@pytest.mark.parametrize('question,mode', [('Explica la sección Integridad', 'detalle'), ('Dame un resumen completo', 'resumen')])
def test_documental_contenido_no_se_convierte_en_validacion(document, monkeypatch, question, mode):
    from proyecto.config import ollama_runtime
    doc = copy.deepcopy(document)
    text = '# Integridad\nEl control de acceso protege los datos de cambios no autorizados.'
    doc['pages'] = [{'source': 'Página 1', 'text': text, 'redacted': False}]
    doc['fragments'][0]['text'] = text
    doc['integrity'] = dq.validate_integrity(doc['pages'])
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': mode,
        'answer': 'El control protege los datos frente a cambios sin autorización.', 'evidence_ids': ['F1']})
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', Mock(return_value=model))
    result = dq.answer(question, doc)
    assert result['status'] == 'respondido' and result['mode'] == mode
    assert 'Validación documental:' not in result['message'] and 'NO_EVALUABLE' not in result['message']


def test_relacionada_solo_publica_citas_reales(monkeypatch, document):
    from proyecto.config import ollama_runtime
    llm = Mock()
    llm.invoke.return_value.content = json.dumps({"answerable": True, "mode": "respuesta", "answer": "El saldo final es de 9100.00.", "quotes": [{"id": "F1", "text": "Saldo final: 9100.00"}]})
    monkeypatch.setattr(ollama_runtime, "ChatOllama", Mock(return_value=llm))
    result = dq.answer("¿Cuál es el saldo final?", document)
    assert "9100.00" in result["message"] and "Página 1" in result["message"]
    llm.invoke.return_value.content = json.dumps({"answerable": True, "mode": "respuesta", "answer": "El saldo es de 100000.", "quotes": [{"id": "F1", "text": "Saldo final: 100000"}]})
    assert dq.answer("¿Cuál es el saldo final?", document)["status"] == "error_tecnico"


def test_ajena_y_ausente_no_invocan_llm(monkeypatch, document):
    from proyecto.config import ollama_runtime
    llm = Mock(side_effect=AssertionError("No debe inferir"))
    monkeypatch.setattr(ollama_runtime, "ChatOllama", llm)
    assert dq.answer("¿Por qué el cielo es azul?", document)["status"] == "fuera_del_documento"
    assert dq.answer("¿Cuál es mi historial crediticio?", document)["status"] == "sin_evidencia"
    llm.assert_not_called()


def test_seguimiento_y_calculo_decimal(monkeypatch, document):
    calls = []
    monkeypatch.setattr(dq, "embed", lambda parts: calls.extend(parts) or [[1., 0.]])
    result = dq.answer("¿Y cuánto suman los cargos?", document, "¿Cuál es el saldo final?")
    assert result["calculation"]["result"] == "900.00"
    assert "Seguimiento:" in calls[0]
    changed = copy.deepcopy(document)
    changed["pages"][0]["text"] += "\n09/09 | Ambiguo | | 10.00"
    assert dq.answer("¿Cuánto suman los cargos?", changed)["status"] == "sin_evidencia"


def test_otras_fuentes_conservan_su_ruta():
    assert dq.external_request("¿Qué tablas están disponibles en la base de datos?")
    assert dq.external_request("¿Qué exige la Ley Federal de protección de datos?")
    assert dq.external_request("¿Cuántos clientes hay?")
    assert not dq.external_request("¿Cuál es mi historial crediticio?")
    assert not dq.external_request("¿Qué dice este PDF sobre la Ley Federal?")


def test_contexto_ligado_a_sesion_y_permisos_reales(monkeypatch, identidad_prueba):
    from proyecto.security import local_auth as auth
    from proyecto.mcp_servers import document_intelligence_server as server
    first = dq.session_owner()
    password = secrets.token_urlsafe(24)
    auth.provision("otro_usuario_sintetico", password, "normal", [1], [])
    other_token = auth.login("otro_usuario_sintetico", password)
    responder = Mock(side_effect=AssertionError("No debe acceder al contenido"))
    monkeypatch.setattr(dq, "answer", responder)
    with auth.authenticated(other_token):
        assert dq.session_owner() != first
        assert server.responder_pregunta_documental("¿Cuál es el saldo final?", {"owner": first})["code"] == "ACCESO_DENEGADO"
    assert dq.session_owner() == first
    assert server.responder_pregunta_documental("Dame el nombre del titular", {"owner": first})["code"] == "ACCESO_DENEGADO"
    responder.assert_not_called()


def test_servicio_no_reutiliza_otro_contexto(monkeypatch):
    from proyecto import servicio
    import streamlit as st
    monkeypatch.setattr(st, "session_state", {"documento_consultable": {"owner": "otro", "index": {}}})
    monkeypatch.setattr(servicio, "_identidad_documental", lambda q: ("actual", "no-es-token-real"))
    result = servicio._preguntar_documento("¿Cuál es el saldo final?")
    assert result["error"] == "autorizacion_denegada"
    assert not st.session_state.get("documento_consultable")


def test_conversion_documental_reutiliza_cliente_compartido(monkeypatch):
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    call = Mock(return_value="texto de prueba controlado")
    monkeypatch.setattr(mcp_client, "llamar_herramienta", call)
    assert servicio._llamar_mcp_document_intelligence("convertir_documento_markdown", "prueba.pdf", campo="file_path") == "texto de prueba controlado"
    call.assert_called_once_with("proyecto.mcp_servers.document_intelligence_server", "convertir_documento_markdown", {"file_path": "prueba.pdf"})


@pytest.mark.parametrize("question", [
    "¿Puedes inventar una historia sobre estas compras?",
    "Escribe un cuento con los movimientos de este PDF",
    "Genera una canción sobre el saldo final",
    "¿Por qué el cielo es azul según este PDF bancario?",
])
def test_alcance_rechaza_finalidad_ajena_aunque_haya_coincidencia(monkeypatch, document, question):
    from proyecto.config import ollama_runtime
    # La coincidencia textual/semantica seria maxima; no habilita otra finalidad.
    document["pages"][0]["text"] += "\nCompras, historia, cuento, canción, cielo azul."
    document["fragments"][0]["text"] = document["pages"][0]["text"]
    embedding = Mock(side_effect=AssertionError("No debe recuperar texto para una finalidad ajena"))
    llm = Mock(side_effect=AssertionError("No debe llamar al LLM"))
    monkeypatch.setattr(dq, "embed", embedding)
    monkeypatch.setattr(ollama_runtime, "ChatOllama", llm)
    result = dq.answer(question, document)
    assert result["code"] == "PREGUNTA_FUERA_DE_ALCANCE"
    assert result["evidence"] == []
    embedding.assert_not_called()
    llm.assert_not_called()


@pytest.mark.parametrize("question", [
    "¿Cuál es el saldo final?", "¿Cuánto suman los cargos?",
    "¿El documento cumple las reglas de calidad?",
    "No inventes una historia; indica el saldo final",
    "Escribe un resumen de la historia del saldo",
    "Verifica si el documento inventa una historia sobre las compras",
])
def test_alcance_conserva_analisis_y_validacion(question):
    from proyecto.orchestration.grafo import fuera_de_alcance
    assert not fuera_de_alcance(question, hay_contexto=True)
    if "documento cumple" in question:
        assert not dq.external_request(question)  # No convertir calidad del PDF en consulta SQLite.


@pytest.mark.parametrize("decision,code", [("rechazado", "DOCUMENTO_FUERA_DE_ALCANCE"),
    ("indeterminado", "DOCUMENTO_INDETERMINADO"), (None, "ERROR_TECNICO")])
def test_alcance_exige_admision(monkeypatch, document, decision, code):
    document["admission"] = {"decision": decision}
    embedding = Mock()
    monkeypatch.setattr(dq, "embed", embedding)
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == code
    embedding.assert_not_called()


def test_alcance_distingue_falta_evidencia_y_error(monkeypatch, document):
    assert dq.answer("¿Cuál es mi historial crediticio completo?", document)["code"] == "SIN_EVIDENCIA"
    monkeypatch.setattr(dq, "embed", Mock(side_effect=dq.SimilarityError("servicio no disponible")))
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == "ERROR_TECNICO"


def test_alcance_respuesta_permitida_y_calculo(monkeypatch, document):
    from proyecto.config import ollama_runtime
    llm = Mock()
    llm.invoke.return_value.content = json.dumps({"answerable": True, "quotes": [{"id": "F1", "text": "Saldo final: 9100.00"}]})
    monkeypatch.setattr(ollama_runtime, "ChatOllama", Mock(return_value=llm))
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == "OK"
    assert dq.answer("¿Cuánto suman los cargos?", document)["calculation"]["result"] == "900.00"
    llm.invoke.return_value.content = "respuesta mal formada"
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == "ERROR_TECNICO"
    llm.invoke.return_value.content = "[]"
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == "ERROR_TECNICO"
    llm.invoke.return_value.content = json.dumps({"answerable": True, "quotes": [{"id": "F1", "text": "dato que no está en el PDF"}]})
    assert dq.answer("¿Cuál es el saldo final?", document)["code"] == "ERROR_TECNICO"


@pytest.mark.parametrize("decision,expected", [("rechazado", "DOCUMENTO_FUERA_DE_ALCANCE"), ("aceptado", None)])
def test_alcance_mcp_prepara_solo_archivo_real_admitido(monkeypatch, decision, expected):
    from types import SimpleNamespace
    from proyecto.mcp_servers import document_intelligence_server as server
    from proyecto.security import local_auth as auth
    from proyecto.agents import document_similarity as ds
    from proyecto.security import document_access as access
    monkeypatch.setattr(auth, "principal", lambda: {"permissions": []})
    monkeypatch.setattr(auth, "authorize_request", lambda q: {})
    monkeypatch.setattr(dq, "session_owner", lambda: "sesion-simulada")
    monkeypatch.setattr(server, "document_to_markdown", SimpleNamespace(invoke=Mock(return_value="Texto del archivo real")))
    monkeypatch.setattr(access, 'extract_authorized', lambda ref: ('referencia-autorizada', {}, 'Texto del archivo real', [], False))
    monkeypatch.setattr(access, 'authorize_file', lambda ref: (ref, {}))
    monkeypatch.setattr(access, 'bind_context', lambda *a: None)
    evaluation = Mock(return_value={"decision": decision})
    monkeypatch.setattr(ds, "evaluate", evaluation)
    prepare = Mock(return_value={"pages": []})
    monkeypatch.setattr(dq, "prepare_context", prepare)
    result = server.preparar_documento_consultable("prueba.pdf", "texto sustituido por el cliente")
    evaluation.assert_called_once_with("Texto del archivo real")
    if expected:
        assert result["code"] == expected
        prepare.assert_not_called()
    else:
        assert result["admission"]["decision"] == "aceptado"
        prepare.assert_called_once_with('referencia-autorizada')


@pytest.mark.parametrize("denied", [True, False])
def test_alcance_mcp_denegacion_y_error_sin_exponer_datos(monkeypatch, denied):
    from proyecto.mcp_servers import document_intelligence_server as server
    from proyecto.security import local_auth as auth
    error = PermissionError() if denied else RuntimeError()
    monkeypatch.setattr(auth, "authorize_request", Mock(side_effect=error))
    responder = Mock()
    monkeypatch.setattr(dq, "answer", responder)
    result = server.responder_pregunta_documental("Consulta", {"owner": "ajeno", "contenido_privado": "no mostrar"})
    assert result["code"] == ("ACCESO_DENEGADO" if denied else "ERROR_TECNICO")
    assert result["evidence"] == [] and "no mostrar" not in result["message"]
    responder.assert_not_called()


def test_alcance_mcp_contexto_ajeno_no_llega_a_respuesta(monkeypatch):
    from proyecto.mcp_servers import document_intelligence_server as server
    from proyecto.security import local_auth as auth
    monkeypatch.setattr(auth, "authorize_request", lambda q: {})
    monkeypatch.setattr(dq, "session_owner", lambda: "sesion-actual")
    responder = Mock()
    monkeypatch.setattr(dq, "answer", responder)
    result = server.responder_pregunta_documental("Saldo", {"owner": "sesion-ajena"})
    assert result["code"] == "ACCESO_DENEGADO" and result["evidence"] == []
    responder.assert_not_called()


def test_alcance_mcp_permiso_antes_de_embeddings(monkeypatch):
    from types import SimpleNamespace
    from proyecto.mcp_servers import document_intelligence_server as server
    from proyecto.security import local_auth as auth
    from proyecto.agents import document_similarity as ds
    from proyecto.security import document_access as access
    monkeypatch.setattr(access, 'extract_authorized', Mock(side_effect=PermissionError()))
    monkeypatch.setattr(auth, "principal", lambda: {"permissions": []})
    monkeypatch.setattr(auth, "authorize_request", Mock(side_effect=PermissionError()))
    monkeypatch.setattr(server, "document_to_markdown", SimpleNamespace(invoke=Mock(return_value="Cuenta 999")))
    evaluation = Mock()
    monkeypatch.setattr(ds, "evaluate", evaluation)
    result = server.preparar_documento_consultable("prueba.pdf", "texto falso")
    assert result["code"] == "ACCESO_DENEGADO" and result["evidence"] == []
    evaluation.assert_not_called()


@pytest.mark.skipif(os.getenv("VALIDAR_PREGUNTAS_MCP_REAL") != "1", reason="Solo lote nuevo MCP/Ollama explicito")
def test_preguntas_mcp_real(tmp_path, identidad_prueba):
    """Lote opcional: cita real y bloqueo de totales si el resumen no es extraible.

    El PDF historico conserva su resumen narrativo, sin campos etiquetados del
    perfil contable actual. No sirve como fixture completo de conciliacion.
    """
    import asyncio
    import hashlib
    import importlib.util
    from pathlib import Path
    from fastmcp import Client
    from proyecto.mcp_servers.mcp_client import crear_transporte
    from proyecto.agents.document_similarity import storage
    from proyecto.security.document_access import store_upload
    from proyecto.security.local_auth import principal
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("pdf_fixture", root / "tests/test_document_similarity.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    example = json.loads((root / "tests/fixtures/document_similarity_validation.json").read_text(encoding="utf-8"))[0]
    pdf = tmp_path / "estado_sintetico.pdf"
    customer = principal()['customers'][0]
    fixture._pdf(pdf, example["text"].replace('Cuenta DEMO-Z9', f'Cliente: {customer}'))
    reference = store_upload(pdf.read_bytes(), '.pdf', pdf.name)
    cache = storage() / "references.json"
    cache_before = cache.read_bytes()
    from proyecto.security.local_auth import principal
    db = Path(principal()["database"])
    db_before = hashlib.sha256(db.read_bytes()).hexdigest()

    async def run():
        async with Client(crear_transporte("proyecto.mcp_servers.document_intelligence_server"), timeout=180) as client:
            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.is_error, name
                return "\n".join(block.text for block in result.content if block.type == "text")
            text = await call("convertir_documento_markdown", {"file_path": reference})
            context = json.loads(await call("preparar_documento_consultable", {"file_path": reference, "texto": text}))
            assert context['integrity']['blocking_conclusion'] is True
            assert {'INCOMPLETO', 'NO_EVALUABLE'} & set(context['integrity']['states'])
            results = []
            previous = ""
            for question in ["¿Cuál es el saldo final?", "¿Y cuánto suman los cargos?", "¿Por qué el cielo es azul?", "¿Cuál es mi historial crediticio?"]:
                result = json.loads(await call("responder_pregunta_documental", {"pregunta": question, "documento": context, "anterior": previous}))
                results.append(result)
                if result["status"] == "respondido":
                    previous = question
            print("PREGUNTAS_MCP_REAL=" + json.dumps(results, ensure_ascii=False))
            assert results[0]["status"] == "respondido" and "9100" in results[0]["message"]
            assert "Página 1" in results[0]["message"]
            assert results[1]['status'] == 'diagnostico_limitado'
            assert results[1]['integrity']['blocking_conclusion'] is True
            assert not results[1].get('calculation') and not results[1].get('model')
            assert results[2]["status"] == "fuera_del_documento" and not results[2].get("model")
            assert results[3]["status"] == "sin_evidencia" and not results[3].get("model")
    asyncio.run(run())
    assert cache.read_bytes() == cache_before
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_before
