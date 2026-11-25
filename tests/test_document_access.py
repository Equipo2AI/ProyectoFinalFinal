"""Seguridad documental: auth real sobre metadatos sinteticos; sin SQLite/LLM."""
import copy
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from proyecto.security import local_auth as auth
from proyecto.security import document_access as access
from proyecto.agents import document_questions as dq
from proyecto.agents import document_similarity as ds
import importlib
agent = importlib.import_module('proyecto.agents.document_intelligence_agent')
from proyecto.mcp_servers import document_intelligence_server as server
REAL_EXTRACT_PAGES = dq.extract_pages
REAL_CONVERT_DOCUMENT = agent._convertir_documento


@pytest.fixture
def secured(tmp_path, monkeypatch):
    root = tmp_path / 'auth'
    root.mkdir()
    monkeypatch.setattr(auth, 'ROOT', root)
    monkeypatch.setattr(auth, 'STATE', root / 'identities.json')
    monkeypatch.setenv('PROYECTO_AUTH_DIR', str(root))
    monkeypatch.setenv('PROYECTO_AUTH_TOKEN', '')
    monkeypatch.setattr(auth, 'db_binding', lambda: ('base-simulada', 'hash-simulado'))
    users = {name: {'role': 'normal', 'customers': [i], 'permissions': [],
                   'database': 'base-simulada', 'digest': 'hash-simulado'}
             for i, name in enumerate(('usuario_a', 'usuario_b'), 1)}
    tokens = {'usuario_a': 'sesion-sintetica-a', 'usuario_b': 'sesion-sintetica-b'}
    auth._write({'users': users, 'sessions': {
        hashlib.sha256(token.encode()).hexdigest(): {'user': name, 'expires': time.time() + 3600}
        for name, token in tokens.items()}})

    def authorize(query):
        user = auth.principal()
        for number, customer in [('100', 1), ('200', 2)]:
            if number in query and customer not in user['customers']:
                raise PermissionError()
        return user
    monkeypatch.setattr(auth, 'authorize_request', authorize)  # lookup de cuentas simulado
    extract = Mock(side_effect=lambda p, **kwargs: p.read_text(encoding='utf-8'))
    monkeypatch.setattr(agent, '_convertir_documento', extract)
    monkeypatch.setattr(dq, 'extract_pages', lambda p, t: ([{'source': 'Página 1', 'text': t}], False))
    embeddings = Mock(side_effect=lambda parts: [[1., 0.] for _ in parts])
    monkeypatch.setattr(dq, 'embed', embeddings)
    monkeypatch.setattr(dq, 'model_digest', lambda: 'modelo-simulado')
    evaluation = Mock(return_value={'decision': 'aceptado', 'model': ds.MODEL})
    monkeypatch.setattr(ds, 'evaluate', evaluation)
    from proyecto.config import ollama_runtime
    model = Mock()
    model.invoke.return_value.content = json.dumps({'answerable': True, 'quotes': [{'id': 'F1', 'text': 'Saldo final: 9100.00'}]})
    factory = Mock(return_value=model)
    monkeypatch.setattr(ollama_runtime, 'ChatOllama', factory)

    @contextmanager
    def as_user(name='usuario_a'):
        with auth.authenticated(tokens[name]):
            yield
    return tokens, as_user, extract, embeddings, evaluation, factory


@pytest.mark.parametrize('indeterminate', [False, True])
def test_chat_concierge_expediente_y_seguimiento(secured, monkeypatch, indeterminate):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, extract, _, admission, _ = secured
    extract.side_effect = lambda p, **kwargs: p.read_text(encoding='utf-8')
    orchestrator = Orquestador(modelo='modelo-no-ejecutado', memory=Mock())
    calls = []
    def mcp(module, name, args):
        assert module == 'proyecto.mcp_servers.document_intelligence_server'
        assert name == 'evaluar_expediente_micuenta'
        calls.append(args['documentos'])
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)  # doble de transporte, backend real
    admission.side_effect = AssertionError('El expediente usa perfiles, no admisión semántica global')
    with as_user():
        refs = [access.store_upload((('AVISO DE PRIVACIDAD\n' if indeterminate else '') + t).encode(), '.pdf', n) for t,n in [
            (CONCIERGE_ID, 'credencial.pdf'), (CONCIERGE_CURP, 'registro.pdf'),
            (CONCIERGE_ADDRESS, 'recibo.pdf')]]
        orchestrator.registrar_adjuntos_chat(refs)
        text = orchestrator.procesar('¿Mis archivos cumplen los requisitos para abrir MiCuenta Banamex?')
        assert ('NO_VERIFICABLE' if indeterminate else 'VALIDO_SEGUN_REGLAS') in text
        assert orchestrator._mensajes[-1].content == text
        follow = orchestrator.procesar('¿Qué documentos faltan?')
        assert calls == [refs, refs]
        assert orchestrator._mensajes[-1].content == follow
        assert orchestrator.agentes_invocados == ['document_intelligence']
    with as_user('usuario_b'):
        with pytest.raises(PermissionError):
            orchestrator.procesar('¿Mi expediente MiCuenta está completo?')
    assert len(calls) == 2


def test_chat_concierge_normal_y_requisitos_sin_modelo(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration.grafo import planear_rutas_hibrido
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    graph = Mock(return_value='ruta previa')
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    with as_user():
        assert orchestrator.procesar('Hola') == 'ruta previa'
        graph.assert_called_once()
        plan = planear_rutas_hibrido('¿Qué requisitos necesito para abrir MiCuenta Banamex?')
        assert plan[0] == ['normatividad_rag']


def test_chat_concierge_pdf_individual_y_ambiguedad(secured, monkeypatch):
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    tokens, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    state = {'auth_token': tokens['usuario_a'], 'orquestador_sesion': orchestrator}
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: orchestrator)
    single = Mock(return_value={'respuesta': 'ruta individual existente'})
    monkeypatch.setattr(servicio, 'procesar_documento', single)
    with as_user():
        first = access.store_upload(TEXT.encode(), '.pdf', 'enero.pdf')
        result = servicio.procesar_adjuntos_chat([(first, 'enero.pdf', 'documento')], '¿Qué saldo muestra este PDF?')
        assert result['respuesta'] == 'ruta individual existente'
        single.assert_called_once_with(first, '¿Qué saldo muestra este PDF?', 'enero.pdf')
        second = access.store_upload(TEXT.encode(), '.pdf', 'febrero.pdf')
        orchestrator.registrar_adjuntos_chat([second])
        result = servicio.preguntar_orquestador('¿Qué saldo muestra el PDF?')
        assert result['code'] == 'ACLARACION_NECESARIA'
        assert single.call_count == 1
        servicio.preguntar_orquestador('¿Qué saldo muestra enero.pdf?')
        assert single.call_count == 2
        assert single.call_args.args[0] == first


def test_chat_concierge_tipo_ambiguo_no_es_requisito_valido(secured, monkeypatch):
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, _, _, admission, _ = secured
    admission.side_effect = AssertionError('No ejecutar embeddings para el expediente')
    with as_user():
        ref = access.store_upload(('AVISO DE PRIVACIDAD\n' + CONCIERGE_ID).encode(), '.pdf', 'ambiguo.pdf')
        result = evaluate_case([ref])
    assert not result['completo_segun_reglas']
    assert all(r['estado'] == 'NO_VERIFICABLE' for r in result['requisitos'] if r['aplica'] is not False)
    admission.assert_not_called()


def test_chat_concierge_lote_desde_servicio_y_sin_archivos(secured, monkeypatch):
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.agents.document_requirements import evaluate_case
    from proyecto.mcp_servers import mcp_client
    tokens, as_user, extract, *_ = secured
    extract.side_effect = lambda p, **kwargs: p.read_text(encoding='utf-8')
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a'], 'orquestador_sesion': orchestrator})
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: orchestrator)
    calls = []
    def mcp(module, name, args):
        assert name == 'evaluar_expediente_micuenta'
        calls.append(args['documentos'])
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)
    with as_user():
        empty = servicio.preguntar_orquestador('¿Mi expediente MiCuenta Banamex está completo?')
        assert 'FALTANTE' in empty['respuesta']
        assert calls == [[]]
        refs = [access.store_upload(TEXT.encode(), '.pdf', name) for name in ['uno.pdf', 'dos.pdf']]
        result = servicio.procesar_adjuntos_chat([(r, n, 'documento') for r,n in zip(refs, ['uno.pdf','dos.pdf'])],
                                              '¿Mis documentos cumplen los requisitos de apertura MiCuenta Banamex?')
        assert calls[-1] == refs
        assert 'NO_VERIFICABLE' in result['respuesta']
        assert result['herramientas'] == ['evaluar_expediente_micuenta']
        assert 'document_intelligence' in result['agentes']
        servicio.preguntar_orquestador('¿Mi expediente MiCuenta está completo usando solo uno.pdf?')
        assert calls[-1] == refs[:1]
        own_calls = len(calls)
    with as_user('usuario_b'):
        with pytest.raises(PermissionError):
            evaluate_case(refs)
    assert len(calls) == own_calls


TEXT = 'Estado de cuenta\nCuenta: 100\nNombre del titular: Persona reservada\nSaldo final: 9100.00\nDatos de prueba, sin informacion real.'


def registered():
    return access.store_upload(TEXT.encode(), '.pdf', 'estado.pdf')


CONCIERGE_ID = 'Identificacion oficial INE\nPersona: SUJETO FICTICIO\nRegistro: ETIQUETA FICTICIA\nVigente hasta: 2027-11-24\nCURP: no incluida'
CONCIERGE_CURP = 'Constancia CURP\nPersona: SUJETO FICTICIO\nEntidad de registro: OFICINA FICTICIA\nCURP: IDENTIFICADOR_FICTICIO_PRIVADO'
CONCIERGE_ADDRESS = 'Comprobante de domicilio\nTitular: SUJETO FICTICIO\nTipo de recibo: luz\nFecha de emision: 2026-11-01\nDomicilio: DIRECCION_FICTICIA_PRIVADA'


def _case_metadata(texts):
    from proyecto.agents.document_requirements import requirement_metadata
    return [{'documento_id': f'doc-fixture-{i}',
             'metadata': requirement_metadata([{'source': 'Página 1', 'text': text}], False)}
            for i, text in enumerate(texts)]


@pytest.mark.parametrize('case,requirement,status,complete', [
    ('vigente', 'ine', 'VALIDO_SEGUN_REGLAS', True),
    ('vencida', 'ine', 'PRESENTE', False),
    ('curp_completa', 'curp', 'VALIDO_SEGUN_REGLAS', True),
    ('curp_incompleta', 'curp', 'PRESENTE', False),
    ('recibo_completo', 'domicilio', 'VALIDO_SEGUN_REGLAS', True),
    ('recibo_incompleto', 'domicilio', 'PRESENTE', False),
    ('faltante', 'domicilio', 'FALTANTE', False),
    ('educativo', 'curp', 'NO_VERIFICABLE', False),
    ('extraccion_parcial', 'ine', 'NO_VERIFICABLE', False),
])
def test_concierge_tipo_separado_de_cumplimiento(case, requirement, status, complete):
    from datetime import date
    from proyecto.agents.document_requirements import requirement_metadata, evaluate_requirement_metadata
    texts = [CONCIERGE_ID, CONCIERGE_CURP, CONCIERGE_ADDRESS]
    if case == 'vencida':
        texts[0] = texts[0].replace('2027-11-24', '2020-01-01')
    elif case == 'curp_incompleta':
        texts[1] = texts[1].replace('IDENTIFICADOR_FICTICIO_PRIVADO', '')
    elif case == 'recibo_incompleto':
        texts[2] = texts[2].replace('DIRECCION_FICTICIA_PRIVADA', '')
    elif case == 'faltante':
        texts.pop(2)
    elif case == 'educativo':
        texts[1] = 'CONSTANCIA CURP: LECTURA COMENTADA\n' + texts[1]
    docs = _case_metadata(texts)
    if case == 'extraccion_parcial':
        docs[0]['metadata'] = requirement_metadata([{'text': texts[0], 'source': 'Página 1'}], True)
    result = evaluate_requirement_metadata(docs, today=date(2026, 11, 25))
    row = next(r for r in result['requisitos'] if r['requisito'] == requirement)
    assert row['estado'] == status
    assert result['completo_segun_reglas'] is complete
    if case == 'vencida':
        assert row['cumple'] is False
        assert {'estado': 'VENCIDO', 'campo': 'vigencia'} in row['incidencias']
    if case in {'curp_incompleta', 'recibo_incompleto'}:
        assert row['incidencias'] and row['campos_no_verificables']
        assert row['cumple'] is not True
    assert result['aprobacion_bancaria'] is None and not result['titularidad_verificada']


def test_concierge_expediente_pdf_real_sin_embeddings_y_sesion_ajena(secured, monkeypatch):
    """Registro y permisos reales de prueba; conversión/PDF reales, sin STDIO ni LLM."""
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, _, _, admission, factory = secured
    monkeypatch.setattr(agent, '_convertir_documento', REAL_CONVERT_DOCUMENT)
    monkeypatch.setattr(dq, 'extract_pages', REAL_EXTRACT_PAGES)
    admission.side_effect = AssertionError('No usar embeddings para perfiles Concierge')
    factory.side_effect = AssertionError('No ejecutar un modelo generativo')
    root = Path(__file__).parent / 'fixtures/concierge_final_pdf_holdout'
    paths = [root / (name + '.pdf') for name in ['ine_completa', 'curp_completa', 'domicilio_completo']]
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    with as_user():
        refs = [access.store_upload(p.read_bytes(), '.pdf', p.name) for p in paths]
        result = evaluate_case(refs)
        assert result['completo_segun_reglas'] is True
        assert all(a['decision'] == 'reconocido' and a['admision_semantica'] == 'NO_EVALUADA'
                   for a in result['admision_semantica'])
        assert not result['titularidad_verificada']
        expired = Path(__file__).parent / 'fixtures/concierge_demo/identificacion_vencida_simulada.pdf'
        expired_ref = access.store_upload(expired.read_bytes(), '.pdf', expired.name)
        expired_result = evaluate_case([expired_ref, *refs[1:]])
        row = next(r for r in expired_result['requisitos'] if r['requisito'] == 'ine')
        assert row['estado'] == 'PRESENTE' and row['cumple'] is False
        assert {'estado': 'VENCIDO', 'campo': 'vigencia'} in row['incidencias']
        educational = root / 'educativo_registro_limpio.pdf'
        educational_ref = access.store_upload(educational.read_bytes(), '.pdf', educational.name)
        uncertain_result = evaluate_case([refs[0], educational_ref, refs[2]])
        curp = next(r for r in uncertain_result['requisitos'] if r['requisito'] == 'curp')
        assert curp['estado'] == 'NO_VERIFICABLE'
        assert not uncertain_result['completo_segun_reglas']
    extractor = Mock(side_effect=AssertionError('No leer documentos ajenos'))
    monkeypatch.setattr(access, 'extract_requirement_metadata', extractor)
    with as_user('usuario_b'):
        with pytest.raises(PermissionError):
            evaluate_case(refs)
    extractor.assert_not_called()
    admission.assert_not_called()
    assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


@pytest.mark.parametrize('embedded', [False, True])
def test_concierge_matriz_condicion_curp_y_sin_pii(embedded):
    from datetime import date
    from proyecto.agents.document_requirements import evaluate_requirement_metadata
    ine = CONCIERGE_ID.replace('no incluida', 'IDENTIFICADOR_FICTICIO_PRIVADO') if embedded else CONCIERGE_ID
    docs = [ine, CONCIERGE_ADDRESS] + ([] if embedded else [CONCIERGE_CURP])
    result = evaluate_requirement_metadata(_case_metadata(docs), today=date(2026, 11, 24))
    rows = {r['requisito']: r for r in result['requisitos']}
    assert result['completo_segun_reglas'] and result['aprobacion_bancaria'] is None
    assert rows['ine']['estado'] == rows['domicilio']['estado'] == 'VALIDO_SEGUN_REGLAS'
    assert rows['curp']['aplica'] == (not embedded)
    assert rows['rfc']['aplica'] is False and rows['rfc']['estado'] is None
    assert result['matriz']['version'] == '2026-11-24.1'
    assert result['matriz']['canal'] == 'sucursal'
    assert result['matriz']['fecha_consulta'] == '2026-11-24'
    assert 'IDENTIFICADOR_FICTICIO_PRIVADO' not in json.dumps(result)
    assert 'DIRECCION_FICTICIA_PRIVADA' not in json.dumps(result)


@pytest.mark.parametrize('change,requirement,status', [
    ('missing_address', 'domicilio', 'FALTANTE'),
    ('missing_curp', 'curp', 'FALTANTE'),
    ('expired', 'ine', 'PRESENTE'),
])
def test_concierge_faltante_no_es_identificacion_vencida(change, requirement, status):
    from datetime import date
    from proyecto.agents.document_requirements import evaluate_requirement_metadata
    texts = [CONCIERGE_ID, CONCIERGE_CURP, CONCIERGE_ADDRESS]
    if change == 'missing_address':
        texts.pop(2)
    elif change == 'missing_curp':
        texts.pop(1)
    else:
        texts[0] = texts[0].replace('2027-11-24', '2025-11-24')
    result = evaluate_requirement_metadata(_case_metadata(texts), today=date(2026, 11, 24))
    row = next(r for r in result['requisitos'] if r['requisito'] == requirement)
    assert row['estado'] == status and row['cumple'] is False
    assert not result['completo_segun_reglas']
    if change == 'expired':
        assert row['incidencias'] == [{'estado': 'VENCIDO', 'campo': 'vigencia'}]


@pytest.mark.parametrize('issued,complies', [('2026-08-24', True), ('2026-08-23', False), ('2026-11-25', False)])
def test_concierge_recibo_tres_meses_calendario(issued, complies):
    from datetime import date
    from proyecto.agents.document_requirements import evaluate_requirement_metadata
    docs = _case_metadata([CONCIERGE_ID, CONCIERGE_CURP, CONCIERGE_ADDRESS.replace('2026-11-01', issued)])
    result = evaluate_requirement_metadata(docs, today=date(2026, 11, 24))
    row = next(r for r in result['requisitos'] if r['requisito'] == 'domicilio')
    assert row['cumple'] == complies


def test_concierge_ausencia_no_se_infiere_de_extraccion_insuficiente():
    from proyecto.agents.document_requirements import evaluate_requirement_metadata
    docs = _case_metadata([CONCIERGE_ID.replace('CURP: no incluida', ''), CONCIERGE_ADDRESS])
    result = evaluate_requirement_metadata(docs)
    assert next(r for r in result['requisitos'] if r['requisito'] == 'curp')['estado'] == 'NO_VERIFICABLE'
    assert not result['completo_segun_reglas']
    result = evaluate_requirement_metadata(_case_metadata(['Texto de tipo desconocido']))
    assert next(r for r in result['requisitos'] if r['requisito'] == 'ine')['estado'] == 'NO_VERIFICABLE'
    result = evaluate_requirement_metadata(_case_metadata([CONCIERGE_ID.replace('no incluida', 'si'), CONCIERGE_ADDRESS]))
    assert next(r for r in result['requisitos'] if r['requisito'] == 'curp')['estado'] == 'NO_VERIFICABLE'


def test_concierge_mcp_autoriza_todo_antes_de_extraer_sin_modelos(secured, monkeypatch):
    _, as_user, extract, embeddings, evaluation, factory = secured
    def convert(path, **kwargs):
        assert kwargs == {'modo': 'texto', 'paginas': None}
        return path.read_text(encoding='utf-8')
    extract.side_effect = convert
    with as_user():
        refs = [access.store_upload(text.encode(), '.txt', 'fixture.txt')
                for text in [CONCIERGE_ID, CONCIERGE_CURP, CONCIERGE_ADDRESS]]
        result = server.evaluar_expediente_micuenta(refs)
        assert result['code'] == 'OK' and result['analisis_llm_habilitado'] is False
        assert 'DIRECCION_FICTICIA_PRIVADA' not in json.dumps(result)
        assert 'IDENTIFICADOR_FICTICIO_PRIVADO' not in json.dumps(result)
    extract.reset_mock()
    with as_user('usuario_b'):
        foreign = server.evaluar_expediente_micuenta(refs)
    assert foreign['code'] == 'ACCESO_DENEGADO' and foreign['requisitos'] == []
    extract.assert_not_called()
    embeddings.assert_not_called()
    evaluation.assert_not_called()
    factory.assert_not_called()


def test_concierge_texto_ilegible_y_error_tecnico_no_son_faltantes(secured):
    _, as_user, extract, *_ = secured
    with as_user():
        ref = access.store_upload(b'Contenido sintetico sin conversion real', '.txt', 'fixture.txt')
        extract.side_effect = None
        extract.return_value = ''
        result = server.evaluar_expediente_micuenta([ref])
        assert result['code'] == 'OK'
        assert next(r for r in result['requisitos'] if r['requisito'] == 'ine')['estado'] == 'NO_VERIFICABLE'
        extract.side_effect = RuntimeError('DETALLE_PRIVADO_NO_PUBLICAR')
        error = server.evaluar_expediente_micuenta([ref])
        assert error['code'] == 'ERROR_TECNICO' and error['requisitos'] == []
        assert 'DETALLE_PRIVADO_NO_PUBLICAR' not in json.dumps(error)


def test_merge_archivo_sin_identidad_no_admite_bypass(secured, tmp_path):
    from proyecto.security.file_access import authorize_file_access
    outsider = tmp_path / 'externo.pdf'
    outsider.write_bytes(b'datos sinteticos')
    with pytest.raises(PermissionError):
        authorize_file_access(outsider, allow_unauthenticated=True)


def test_merge_imagen_registrada_y_auditor_ajeno(secured, monkeypatch):
    _, as_user, *_ = secured
    vision = Mock(return_value='Comprobante bancario sintetico\nTotal: 20.00')
    monkeypatch.setattr(agent, '_vision', vision)
    with as_user():
        reference = access.store_upload(b'imagen simulada', '.png', 'comprobante.png')
        assert '20.00' in server.extraer_texto_imagen(reference)
        import importlib
        orchestration = importlib.import_module('proyecto.orchestration.orquestador')
        memory = Mock()
        orq = orchestration.Orquestador(memory=memory)
        orq.registrar_imagen(reference, 'Extraer texto')
        assert reference in orq._document_references
        memory.update.assert_not_called()
    data = auth._read()
    data['users']['usuario_b'].update(role='auditor', permissions=['audit'])
    auth._write(data)
    with as_user('usuario_b'), pytest.raises(PermissionError):
        server.extraer_texto_imagen(reference)
    with as_user('usuario_b'), pytest.raises(PermissionError):
        orq.marcar_imagen_pendiente(reference)
    assert vision.call_count == 1


def test_merge_pdf_directo_exige_registro_y_conserva_modo_paginas(secured, monkeypatch):
    from proyecto.agents import pdf_reader
    _, as_user, *_ = secured
    convert = Mock(return_value='Contenido PDF sintetico autorizado')
    monkeypatch.setattr(pdf_reader, '_pdf_to_markdown', convert)
    with pytest.raises(PermissionError):
        pdf_reader.pdf_to_markdown('ruta-no-registrada.pdf')
    convert.assert_not_called()
    with as_user():
        reference = registered()
        assert 'autorizado' in pdf_reader.pdf_to_markdown(reference, modo='texto', paginas='1-2')
        assert convert.call_args.args[1:3] == ('texto', '1-2')


def test_merge_ocr_no_rasteriza_sin_fuente_autorizada(secured, tmp_path):
    from proyecto.agents import pdf_reader
    page = Mock()
    with pytest.raises(PermissionError):
        pdf_reader._ocr_pagina(page, 1, 'modelo-simulado', 'texto', tmp_path)
    page.render.assert_not_called()


def test_merge_orquestador_temporal_sin_sqlite_ni_memoria_persistente(secured, monkeypatch):
    _, as_user, *_ = secured
    import importlib
    orchestration = importlib.import_module('proyecto.orchestration.orquestador')
    from proyecto.orchestration import grafo
    persistent = Mock(side_effect=AssertionError('No abrir historial SQLite'))
    monkeypatch.setattr(orchestration, 'DocumentContextStore', persistent)
    memory = Mock()
    orq = orchestration.Orquestador(memory=memory)
    execute = Mock(return_value={'respuesta': 'Evidencia simulada autorizada', 'plan': ['directo'], 'trazas': []})
    monkeypatch.setattr(grafo, 'ejecutar_grafo', execute)
    with as_user():
        reference = registered()
        _, entry = access.authorize_file(reference)
        orq.registrar_documento(entry['path'], 'markdown', TEXT, 'estado.pdf')
        assert '9100.00' in orq._contexto_documentos('saldo')
        assert 'autorizada' in orq.procesar('¿Qué dice el documento sobre el saldo?')
        assert execute.call_args.kwargs['document_references'] == [reference]
    persistent.assert_not_called()
    memory.prepare.assert_not_called()
    memory.update.assert_not_called()
    with as_user('usuario_b'), pytest.raises(PermissionError):
        orq.procesar('¿Qué dice el documento sobre el saldo?')
    assert not orq._documentos and not orq._mensajes


def test_merge_grafo_pdf_con_admision_y_sin_segunda_generacion(secured, monkeypatch):
    _, as_user, *_ = secured
    from proyecto.orchestration import grafo
    calls = []
    def mcp(module, name, args):
        calls.append(name)
        return json.dumps(getattr(server, name)(**args))
    monkeypatch.setattr(grafo, '_llamar_mcp', mcp)
    synthesis = Mock(side_effect=AssertionError('No regenerar evidencia documental'))
    monkeypatch.setattr(grafo, '_respuesta_documento', synthesis)
    with as_user():
        reference = registered()
        _, entry = access.authorize_file(reference)
        state = {'consulta': f"¿Cuál es el saldo final del documento '{entry['path']}'?"}
        result = grafo._nodo_documento(state)
        final = grafo._nodo_sintetizar({**state, **result, 'plan': ['document_intelligence']})
        assert '9100.00' in final['respuesta'] and final['redactada']
    assert calls == ['preparar_documento_consultable', 'responder_pregunta_documental']
    synthesis.assert_not_called()


def test_merge_cumplimiento_informativo_usa_rag_y_no_aprueba_personas():
    from proyecto.orchestration import grafo
    assert grafo.planear_rutas('¿Qué establece la normativa KYC?') == ['normatividad_rag']
    assert grafo._es_consulta_normativa('¿Qué es KYC?')
    assert grafo.fuera_de_alcance('Aprueba el cumplimiento AML del cliente 1')


def test_merge_seguimiento_directo_no_reutiliza_pdf_eliminado(secured, monkeypatch):
    _, as_user, *_ = secured
    import importlib
    orchestration = importlib.import_module('proyecto.orchestration.orquestador')
    from proyecto.orchestration import grafo
    execute = Mock(return_value={'respuesta': 'Dato autorizado: 9100.00', 'plan': ['directo'], 'trazas': []})
    monkeypatch.setattr(grafo, 'ejecutar_grafo', execute)
    orq = orchestration.Orquestador(memory=Mock())
    with as_user():
        reference = registered()
        _, entry = access.authorize_file(reference)
        orq.procesar(f"¿Cuál es el saldo del documento '{entry['path']}'?")
        assert reference in orq._document_references
        access.clear_session_documents()
        with pytest.raises(PermissionError):
            orq.procesar('¿Y el saldo?')
        assert not orq._mensajes and not orq._document_references
    assert execute.call_count == 1


@pytest.mark.parametrize('agent_name,question', [('data_steward', 'Calidad de datos'), ('data_analyst', 'Lista de tablas disponibles')])
def test_merge_perfil_no_reescribe_resultados_verificados(agent_name, question):
    from proyecto.orchestration.perfil_respuesta import detectar_perfil, redactar_respuesta
    llm = Mock(side_effect=AssertionError('No reformular datos verificados'))
    answer = 'Resultado verificado con 18 tablas; dim_txn.'
    assert redactar_respuesta(question, answer, detectar_perfil('normal', question),
                             {agent_name: {'respuesta': answer}}, 'modelo-simulado', llamar_modelo=llm) == answer
    llm.assert_not_called()


def test_integridad_mcp_autoriza_admite_y_firma_informe(secured, monkeypatch):
    _, as_user, _, embeddings, evaluation, _ = secured
    original = dq.validate_integrity
    validations = []
    def checked(pages, incomplete=False, **kwargs):
        assert auth.principal()['subject'] == 'usuario_a'
        assert evaluation.called
        assert not embeddings.called  # contexto de preguntas aun no vectorizado
        validations.append(pages)
        return original(pages, incomplete, **kwargs)
    monkeypatch.setattr(dq, 'validate_integrity', checked)
    with as_user():
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        assert context['integrity']['state'] == 'INCOMPLETO'
        access.authorize_context(context)
        result = server.responder_pregunta_documental('¿Qué inconsistencias tiene el documento?', context)
        assert result['status'] == 'diagnostico_limitado'
    assert len(validations) == 1
    with as_user('usuario_b'):
        assert server.preparar_documento_consultable(reference, '')['code'] == 'ACCESO_DENEGADO'
        assert server.responder_pregunta_documental('¿Qué inconsistencias tiene el documento?', context)['code'] == 'ACCESO_DENEGADO'
    assert len(validations) == 1


def test_integridad_servicio_entrega_resumen_sin_llm_y_no_admite_informe_alterado(secured, monkeypatch):
    tokens, as_user, _, _, _, factory = secured
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    import streamlit as st
    class State(dict):
        __getattr__ = dict.get
        __setattr__ = dict.__setitem__
    state = State(auth_token=tokens['usuario_a'])
    monkeypatch.setattr(st, 'session_state', state)
    calls = []
    def call(module, name, args):
        calls.append(name)
        value = getattr(server, name)(**args)
        return json.dumps(value) if isinstance(value, dict) else value
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', call)
    with as_user():
        reference = registered()
    result = servicio.procesar_documento(reference, '¿Qué inconsistencias tiene el documento?')
    assert result['integridad_documental']['state'] == 'INCOMPLETO'
    assert 'INCOMPLETO' in result['respuesta']
    assert 'preparar_documento_consultable' in calls and 'responder_pregunta_documental' in calls
    context = state.documento_consultable['index']
    context['integrity']['blocking_conclusion'] = False
    with as_user():
        assert server.responder_pregunta_documental('¿Qué inconsistencias tiene el documento?', context)['code'] == 'ACCESO_DENEGADO'
    factory.assert_not_called()


def test_integridad_no_muestra_informe_tras_revocacion_en_servicio(secured, monkeypatch):
    tokens, as_user, *_ = secured
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    import streamlit as st
    class State(dict):
        __getattr__ = dict.get
        __setattr__ = dict.__setitem__
    monkeypatch.setattr(st, 'session_state', State(auth_token=tokens['usuario_a']))
    def call(module, name, args):
        value = getattr(server, name)(**args)
        return json.dumps(value) if isinstance(value, dict) else value
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', call)
    def revoke(*args, **kwargs):
        data = auth._read()
        data['users']['usuario_a']['customers'] = [2]
        auth._write(data)
        return {'respuesta': 'Acceso denegado.', 'code': 'ACCESO_DENEGADO'}
    monkeypatch.setattr(servicio, '_responder_con_archivo', revoke)
    with as_user():
        reference = registered()
    result = servicio.procesar_documento(reference, '¿Qué inconsistencias tiene el documento?')
    assert result['code'] == 'ACCESO_DENEGADO'
    assert 'integridad_documental' not in result
    assert '9100' not in repr(result)


def test_temporales_logout_conserva_otra_sesion_y_referencias(secured, tmp_path):
    tokens, as_user, *_ = secured
    cache = tmp_path / 'document_similarity' / 'references.json'
    cache.parent.mkdir()
    cache.write_bytes(b'cache-referencias-intacto')
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    with as_user('usuario_b'):
        other = registered()
        _, other_entry = access.authorize_file(other)
    auth.logout(tokens['usuario_a'])
    assert not Path(entry['path']).exists()
    assert Path(other_entry['path']).exists()
    assert cache.read_bytes() == b'cache-referencias-intacto'


def test_temporales_expiracion_acotada_y_huerfanos_conservados(secured):
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    orphan = Path(entry['path']).parent / ('doc_' + 'f' * 48 + '.pdf')
    orphan.write_bytes(b'huerfano sin registro: no borrar')
    data = auth._read()
    data['sessions'][hashlib.sha256(tokens['usuario_a'].encode()).hexdigest()]['expires'] = 0
    auth._write(data)
    assert access.cleanup_expired()['deleted'] == 1
    assert not Path(entry['path']).exists()
    assert orphan.exists()
    with pytest.raises(PermissionError), as_user():
        pass


def test_temporales_limpiar_chat_invalida_y_elimina(secured):
    import ast
    from types import SimpleNamespace
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    class State(dict):
        __getattr__ = dict.get
        __setattr__ = dict.__setitem__
    state = State(auth_token=tokens['usuario_a'], documento_consultable={'privado': True},
                  history_document_ids=[ref], document_history_binding='anterior')
    tree = ast.parse(Path('proyecto/ui/app.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'limpiar')
    namespace = {'st': SimpleNamespace(session_state=state, warning=Mock(), rerun=Mock()),
                 'authenticated': auth.authenticated, '_limpiar_archivo': Mock(),
                 'reiniciar_orquestador': Mock()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<limpiar-real>', 'exec'), namespace)
    namespace['limpiar']()
    assert not Path(entry['path']).exists()
    assert 'documento_consultable' not in state and 'history_document_ids' not in state
    assert state.mensajes == []
    with as_user(), pytest.raises(PermissionError):
        access.authorize_file(ref)


def test_temporales_bloqueado_reintenta_sin_afirmar_eliminacion(secured, monkeypatch):
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    path = Path(entry['path'])
    original = Path.unlink
    def blocked(p, *a, **kw):
        if p == path:
            raise PermissionError('bloqueado simulado')
        return original(p, *a, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', blocked)
        auth.logout(tokens['usuario_a'])
    assert path.exists()
    data = auth._read()
    record = data['sessions'][hashlib.sha256(tokens['usuario_a'].encode()).hexdigest()]['documents'][ref]
    assert record['delete_pending'] and record['cleanup_error']
    assert access.cleanup_expired()['deleted'] == 1
    assert not path.exists()


def test_temporales_operacion_activa_difiere_borrado(secured):
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    with access._document_io():
        auth.logout(tokens['usuario_a'])
        assert Path(entry['path']).exists()
    assert access.cleanup_expired()['deleted'] == 1
    assert not Path(entry['path']).exists()


@pytest.mark.parametrize('manipulation', ['external', 'other', 'symlink'])
def test_temporales_rechaza_rutas_manipuladas(secured, tmp_path, manipulation):
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    target = tmp_path / 'externo.pdf'
    target.write_bytes(TEXT.encode())
    data = auth._read()
    record = data['sessions'][hashlib.sha256(tokens['usuario_a'].encode()).hexdigest()]['documents'][ref]
    if manipulation == 'symlink':
        path = Path(entry['path'])
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip('Windows no permite crear symlinks sin privilegios; no se cambian permisos.')
    elif manipulation == 'other':
        with as_user('usuario_b'):
            other = registered()
            _, other_entry = access.authorize_file(other)
        data = auth._read()
        record = data['sessions'][hashlib.sha256(tokens['usuario_a'].encode()).hexdigest()]['documents'][ref]
        record['path'] = other_entry['path']
        auth._write(data)
    else:
        record['path'] = str(target)
        auth._write(data)
    auth.logout(tokens['usuario_a'])
    assert target.exists()
    if manipulation == 'other':
        assert Path(other_entry['path']).exists()
    assert access.cleanup_expired()['pending'] == 1


def test_temporales_rechaza_reparse_point_sin_privilegios(secured, monkeypatch):
    from types import SimpleNamespace
    tokens, as_user, *_ = secured
    with as_user():
        ref = registered()
        _, entry = access.authorize_file(ref)
    path = Path(entry['path'])
    original = Path.lstat
    def reparse(p, *args, **kwargs):
        info = original(p, *args, **kwargs)
        if p == path.parent:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', reparse)
        auth.logout(tokens['usuario_a'])
    assert path.exists()
    assert access.cleanup_expired()['deleted'] == 1


def test_temporales_limite_conserva_pendientes_para_siguiente_llamada(secured):
    _, as_user, *_ = secured
    with as_user():
        for _ in range(65):
            registered()
        result = access.clear_session_documents()
        assert result == {'deleted': 64, 'pending': 1}
        assert access.cleanup_expired() == {'deleted': 1, 'pending': 0}


def test_propietario_autorizado_y_flujo_mcp(secured):
    _, as_user, _, embeddings, evaluation, factory = secured
    with as_user():
        reference = registered()
        text = server.convertir_documento_markdown(reference)
        assert 'Persona reservada' not in text
        assert server.validar_tipo_documental('texto manipulado', reference)['decision'] == 'aceptado'
        assert 'texto manipulado' not in evaluation.call_args.args[0]
        context = server.preparar_documento_consultable(reference, 'ignorado')
        assert context['admission']['decision'] == 'aceptado'
        result = server.responder_pregunta_documental('¿Cuál es el saldo final?', context)
        assert result['code'] == 'OK' and '9100.00' in result['message']
        assert 'Persona reservada' not in repr(embeddings.call_args_list)
        assert 'Persona reservada' not in repr(factory.return_value.invoke.call_args)


def test_archivo_ajeno_denegado_antes_de_lectura(secured, monkeypatch):
    _, as_user, extract, _, evaluation, factory = secured
    with as_user():
        reference = registered()
        _, entry = access.authorize_file(reference)
    extract.reset_mock()
    original = Path.read_bytes
    def forbid(path):
        if str(path) == entry['path']:
            raise AssertionError('Archivo ajeno leido')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', forbid)
    with as_user('usuario_b'):
        with pytest.raises(PermissionError):
            server.convertir_documento_markdown(entry['path'])
        assert server.preparar_documento_consultable(reference, '')['code'] == 'ACCESO_DENEGADO'
    extract.assert_not_called()
    evaluation.assert_not_called()
    factory.assert_not_called()


def test_mcp_anonimo_no_extrae_ni_vectoriza(secured):
    _, as_user, extract, embeddings, evaluation, factory = secured
    with as_user():
        reference = registered()
    with pytest.raises(PermissionError):
        server.convertir_documento_markdown(reference)
    assert server.validar_tipo_documental('contenido privado sin referencia')['code'] == 'ACCESO_DENEGADO'
    extract.assert_not_called()
    embeddings.assert_not_called()
    evaluation.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize('protected', ['Cuenta: 200', 'Estado de cuenta\nCuenta: 200',
    'Cuenta: 100 Cliente: 200', 'Cuenta: ****200', 'Cuenta numero: ****200',
    'Cuenta200', 'Cuenta\n200', '| Cuenta | Titular |\n| 200 | Persona |',
    'Nombre del titular\nPersona reservada', 'Titular:\nPersona reservada',
    'num_cta: 200', '| num_cte | nombre |\n| 2 | Persona |'])
def test_fragmento_denegado_antes_de_embeddings(secured, monkeypatch, protected):
    _, as_user, _, embeddings, evaluation, factory = secured
    # La representacion PDF detecta informacion que no aparecia en Markdown.
    monkeypatch.setattr(dq, 'extract_pages', lambda p, t: ([{'source': 'Página 1', 'text': protected}], False))
    with as_user():
        reference = registered()
        result = server.preparar_documento_consultable(reference, '')
        assert result['code'] == 'ACCESO_DENEGADO'
    embeddings.assert_not_called()
    evaluation.assert_not_called()
    factory.assert_not_called()


def test_contexto_alterado_no_llega_a_llm(secured):
    _, as_user, _, _, _, factory = secured
    with as_user():
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        context['pages'][0]['text'] = 'datos sustituidos'
        assert server.responder_pregunta_documental('Saldo', context)['code'] == 'ACCESO_DENEGADO'
    factory.assert_not_called()


@pytest.mark.parametrize('change', ['permissions', 'scope', 'expire', 'logout'])
def test_revocacion_invalida_contexto_e_historial(secured, change):
    tokens, as_user, _, _, _, factory = secured
    with as_user():
        if change == 'permissions':
            data = auth._read()
            data['users']['usuario_a'].update(role='auditor', permissions=['aggregate', 'pii:nombre'])
            auth._write(data)
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        state = {'documento_consultable': {'index': context}, 'document_history_binding': access.binding(),
                 'history_document_ids': [reference], 'mensajes': [{'texto': 'informacion privada'}],
                 'artefacto': {'respuesta': 'informacion privada'}, 'orquestador_sesion': object()}
        data = auth._read()
        if change == 'permissions':
            data['users']['usuario_a']['permissions'] = ['aggregate']
            auth._write(data)
        elif change == 'scope':
            data['users']['usuario_a']['customers'] = [2]
            auth._write(data)
        elif change == 'expire':
            next(s for s in data['sessions'].values() if s['user'] == 'usuario_a')['expires'] = 0
            auth._write(data)
        else:
            auth.logout(tokens['usuario_a'])
        access.invalidate_visible_state(state)
        assert not state.get('mensajes') and not state.get('artefacto') and not state.get('documento_consultable')
        assert server.responder_pregunta_documental('Saldo', context)['code'] == 'ACCESO_DENEGADO'
    factory.assert_not_called()


def test_revocacion_durante_respuesta_impide_divulgacion(secured, monkeypatch):
    _, as_user, _, _, _, _ = secured
    with as_user():
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        def revoke(*args):
            data = auth._read()
            data['users']['usuario_a']['permissions'] = ['aggregate']
            auth._write(data)
            return {'code': 'OK', 'status': 'respondido', 'message': 'no publicar'}
        monkeypatch.setattr(dq, 'answer', revoke)
        result = server.responder_pregunta_documental('Saldo', context)
        assert result['code'] == 'ACCESO_DENEGADO' and 'no publicar' not in result['message']


@pytest.mark.parametrize('fallback', [False, True])
def test_servicio_mantiene_autorizacion_en_mcp_y_fallback(secured, monkeypatch, fallback):
    tokens, as_user, _, _, _, _ = secured
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    import streamlit as st
    class State(dict):
        __getattr__ = dict.get
        __setattr__ = dict.__setitem__
    monkeypatch.setattr(st, 'session_state', State(auth_token=tokens['usuario_a']))
    def call(module, name, args):
        assert auth.principal()['subject'] == 'usuario_a'
        if name == 'convertir_documento_markdown' and fallback:
            raise RuntimeError('transporte simulado no disponible')
        value = getattr(server, name)(**args)
        return json.dumps(value) if isinstance(value, dict) else value
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', call)
    with as_user():
        reference = registered()
    result = servicio.procesar_documento(reference, '¿Cuál es el saldo final?')
    assert result['code'] == 'OK' and '9100.00' in result['respuesta']


def test_respaldo_local_no_convierte_denegacion_en_error_tecnico(secured, monkeypatch):
    tokens, as_user, _, embeddings, evaluation, factory = secured
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    import streamlit as st
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a']})
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', Mock(side_effect=RuntimeError('transporte simulado')))
    with as_user():
        reference = access.store_upload(b'Cuenta: 200', '.pdf', 'no_autorizado.pdf')
    result = servicio.procesar_documento(reference, 'Saldo')
    assert result['code'] == 'ACCESO_DENEGADO'
    embeddings.assert_not_called()
    evaluation.assert_not_called()
    factory.assert_not_called()


def test_metadatos_y_markdown_no_filtran_campos_personales(secured, monkeypatch):
    _, as_user, _, embeddings, _, _ = secured
    monkeypatch.setattr(dq, 'extract_pages', REAL_EXTRACT_PAGES)
    with as_user():
        text = '# Nombre del titular: Persona reservada\nCuenta: 100\nSaldo final: 9100.00'
        reference = access.store_upload(text.encode(), '.txt', 'datos.txt')
        context = server.preparar_documento_consultable(reference, '')
        assert context['admission']['decision'] == 'aceptado'
        assert 'Persona reservada' not in json.dumps(context)
        assert context['pages'][0]['source'] == 'Sección 1'
        assert 'Persona reservada' not in repr(embeddings.call_args_list)


def test_transporte_comparte_identidad_y_almacen_existentes(secured):
    tokens, as_user, _, _, _, _ = secured
    from proyecto.mcp_servers.mcp_client import crear_transporte
    with as_user():
        transport = crear_transporte('proyecto.mcp_servers.document_intelligence_server')
        assert transport.env['PROYECTO_AUTH_TOKEN'] == tokens['usuario_a']
        assert transport.env['PROYECTO_AUTH_DIR'] == str(auth.ROOT)


def test_revocacion_detiene_siguientes_lotes_de_embeddings(secured, monkeypatch):
    _, as_user, _, _, _, factory = secured
    calls = []
    def first_batch(parts):
        calls.append(parts)
        data = auth._read()
        data['users']['usuario_a']['customers'] = [2]
        auth._write(data)
        return [[1., 0.] for _ in parts]
    monkeypatch.setattr(dq, 'embed', first_batch)
    with as_user():
        text = 'Cuenta: 100\n' + ('Informacion autorizada de prueba sobre saldos.\n' * 200)
        reference = access.store_upload(text.encode(), '.pdf', 'largo.pdf')
        result = server.preparar_documento_consultable(reference, '')
        assert result['code'] == 'ACCESO_DENEGADO'
    assert len(calls) == 1
    factory.assert_not_called()


def test_revocacion_en_recuperacion_no_inicia_llm(secured, monkeypatch):
    _, as_user, _, _, _, factory = secured
    with as_user():
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        def revoke(parts):
            data = auth._read()
            data['users']['usuario_a']['customers'] = [2]
            auth._write(data)
            return [[1., 0.]]
        monkeypatch.setattr(dq, 'embed', revoke)
        result = server.responder_pregunta_documental('Saldo final', context)
        assert result['code'] == 'ACCESO_DENEGADO'
    factory.assert_not_called()


def test_seguimiento_no_inyecta_consulta_sin_permiso(secured):
    _, as_user, _, embeddings, _, factory = secured
    with as_user():
        reference = registered()
        context = server.preparar_documento_consultable(reference, '')
        embeddings.reset_mock()
        result = server.responder_pregunta_documental('¿Y el saldo?', context, 'Cuenta 200')
        assert result['code'] == 'ACCESO_DENEGADO'
    embeddings.assert_not_called()
    factory.assert_not_called()
