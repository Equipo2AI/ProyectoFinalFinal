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
from proyecto.orchestration import grafo as conversational_graph
REAL_CONVERSATIONAL_PLAN = conversational_graph._plan_llm


def _contexto_financiero_sintetico(o, periodo, nombre):
    """Índice firmado simulado: estos casos no prueban extracción ni embeddings."""
    ref = access.store_upload(b'fixture-sin-extraccion', '.pdf', nombre)
    pages = [{'source': 'Página 1', 'text': 'Estado de cuenta débito\nPeriodo: ' + periodo}]
    index = {'document_id': ref, 'name': nombre, 'pages': pages, 'incomplete': False,
             'metadata': dq.document_metadata(pages), 'admission': {'decision': 'aceptado'}}
    access.bind_context(ref, index)
    o.registrar_contexto_consultable(index)
    return index


@pytest.mark.parametrize('goal', ['datos', 'aclarar'])
def test_flujo_pdf_metadatos_prioridad_y_sql_explicito(secured, monkeypatch, goal):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='consulta', objetivo=goal, producto=None, requisito=None,
                            referencia_clara=True, foco='general', usar_resultado=False,
                            necesidades=[{'tipo': 'datos' if goal == 'datos' else 'aclaracion', 'consulta': q}])
        return ['data_analyst'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    with as_user():
        o = Orquestador(memory=Mock())
        _contexto_financiero_sintetico(o, 'septiembre 2025', 'periodo.pdf')
        o._expediente_en_curso = True  # El trámite previo no captura el estado de cuenta.
        reading = o.interpretar_turno('Grafica los gastos de septiembre')
        assert reading['turno']['objetivo'] == 'documento'
        assert reading['turno']['fuente_documental'] is True
        context = o.contexto_conversacional()
        assert context['documentos'][0]['metadatos']['facts']['periodo'] == ['septiembre 2025']
        assert 'index' not in context['documentos'][0]
        if goal == 'datos':
            assert o.interpretar_turno('Grafica los retiros de SQLite')['plan'][0] == ['data_analyst']


def test_flujo_referente_propuesto_permite_requisitos_sin_elegir_otro(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='consulta', objetivo='requisitos', producto='MiCuenta Banamex', requisito='ine',
            foco='requisitos', referencia_clara=True, usar_resultado=False,
            necesidades=[{'tipo': 'requisito_producto', 'consulta': q}])
        return ['directo'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    with as_user():
        o = Orquestador(memory=Mock())
        o._apertura_scope = access.binding()
        o._apertura_contexto = {'activa': True, 'producto': None, 'producto_propuesto': 'MiCuenta Banamex'}
        o.procesar('Me interesa esa alternativa; sigamos con ella.')
        assert o.ultima_seleccion_fuentes == ['requisito_producto']
        assert o._apertura_contexto['producto'] == 'MiCuenta Banamex'
        assert o._apertura_contexto['confirmado'] is False  # Referente conocido, no selección inventada.


@pytest.mark.parametrize('role', ['normal', 'auditor'])
def test_flujo_contrato_plan_user_estructurado_sin_cambiar_admin(monkeypatch, role):
    observed = []
    class Model:
        def __init__(self, **kwargs): observed.append(kwargs['format'])
        def invoke(self, messages):
            return Mock(content=json.dumps({'plan': ['directo'], 'subpreguntas': [], 'visualizacion': None,
                'necesidades': [{'tipo': 'conversacion', 'consulta': 'Orientación'}], 'turno': {
                    'acto': 'consulta', 'objetivo': 'apertura', 'producto': None, 'requisito': None,
                    'referencia_clara': False, 'usar_resultado': False, 'foco': 'general'}}))
    monkeypatch.setattr(conversational_graph, 'ChatOllama', Model)
    reading = {}
    assert REAL_CONVERSATIONAL_PLAN('Orientación', {}, 'prueba',
        contexto_conversacional={'rol_autenticado': role, 'producto_documentado': 'Cuenta de prueba'}, lectura_turno=reading)
    if role == 'normal':
        schema = observed[0]
        assert schema['properties']['turno']['additionalProperties'] is False
        assert 'turno' not in schema['properties']['turno']['properties']
        assert 'apertura' in schema['properties']['turno']['properties']['objetivo']['enum']
    else:
        assert observed == ['json']


def test_flujo_pdf_periodo_correccion_y_grafica_no_implementada(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto import servicio
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    monkeypatch.setattr(o, 'redactar_concierge', lambda q, facts: '\n'.join(facts))
    qa = Mock(side_effect=AssertionError('No consultar otro origen ni inventar gráfica'))
    monkeypatch.setattr(servicio, '_preguntar_documento', qa)
    with as_user():
        _contexto_financiero_sintetico(o, 'febrero 2025', 'febrero.pdf')
        reading = {'turno': {'acto': 'consulta', 'fuente_documental': True}}
        mismatch = servicio._consultar_contextos_registrados(o, 'Grafica mis gastos de marzo', reading, 'fixture')
        assert mismatch['code'] == 'ACLARACION_NECESARIA' and 'febrero' in mismatch['respuesta']
        corrected = {'turno': {'acto': 'correccion', 'fuente_documental': True, 'referencia_clara': True},
                     'fuentes': [{'tipo': 'evaluacion_documental', 'consulta': 'Grafica mis gastos de febrero 2025'}]}
        result = servicio._consultar_contextos_registrados(o, 'Era febrero, no marzo', corrected, 'fixture')
        assert result['code'] == 'CAPACIDAD_NO_DISPONIBLE' and result['fuentes'] == ['febrero.pdf']
        assert 'grafica' not in result
        following = {'turno': {'acto': 'seguimiento', 'fuente_documental': True}}
        assert servicio._consultar_contextos_registrados(o, 'Entonces grafícalo', following, 'fixture')['code'] == 'CAPACIDAD_NO_DISPONIBLE'
    qa.assert_not_called()


def test_flujo_pdf_dos_periodos_elige_evidencia_y_ambiguedad(secured, monkeypatch):
    import streamlit as st
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto import servicio
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    state = {}
    monkeypatch.setattr(st, 'session_state', state)
    monkeypatch.setattr(o, 'redactar_concierge', lambda q, facts: '\n'.join(facts))
    qa = Mock(return_value={'respuesta': 'Resumen simulado del documento seleccionado'})
    monkeypatch.setattr(servicio, '_preguntar_documento', qa)
    with as_user():
        _contexto_financiero_sintetico(o, 'febrero 2025', 'febrero.pdf')
        expected = _contexto_financiero_sintetico(o, 'septiembre 2025', 'septiembre.pdf')
        reading = {'turno': {'acto': 'consulta', 'fuente_documental': True}}
        servicio._consultar_contextos_registrados(o, 'Resume septiembre', reading, 'fixture')
        assert state['documento_consultable']['index']['document_id'] == expected['document_id']
        qa.assert_called_once_with('Resume septiembre')
        qa.reset_mock()
        assert servicio._consultar_contextos_registrados(o, 'Resume el estado', reading, 'fixture')['code'] == 'ACLARACION_NECESARIA'
        qa.assert_not_called()
    with as_user('usuario_b'), pytest.raises(PermissionError):
        o.contextos_consultables()


def test_flujo_voz_directa_user_usa_hechos_sin_menu_y_conserva_propuesta(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    observed = []
    def writer(_, messages):
        observed.append(messages)
        return 'Puedo orientarte sobre Cuenta de prueba. ¿Te interesa esa opción?'
    monkeypatch.setattr(style, '_llamar_ollama', writer)
    state = {'consulta': 'Quiero orientación', 'perfil': {'rol': 'usuario'}, 'historial': [],
             'plan_conversacional': {'contexto': {'producto_documentado': 'Cuenta de prueba',
                'requisitos_documentados': {'canal': 'sucursal', 'version': 'version-interna'}}}}
    assert 'Cuenta de prueba' in conversational_graph._respuesta_directa(state)
    assert len(observed) == 1 and 'seguros e inversiones' not in observed[0][0]['content']
    assert 'version-interna' not in observed[0][0]['content']
    with as_user():
        o = Orquestador(memory=Mock())
        monkeypatch.setattr(conversational_graph, 'ejecutar_grafo', lambda *a, **kw: {
            'respuesta': 'Puedo orientarte sobre MiCuenta Banamex. ¿Te interesa?', 'plan': ['directo'], 'trazas': []})
        o._lectura_activa = {'plan': (['directo'], [], ''), 'turno': {}}
        o._procesar_grafo('Quiero orientación')
        assert o._apertura_contexto['producto'] is None
        assert o._apertura_contexto['producto_propuesto'] == 'MiCuenta Banamex'


def test_auditoria_producto_propuesto_mencionado_y_confirmado(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='confirmacion' if q == 'Me convence esa alternativa' else 'consulta',
            objetivo='requisitos', producto='MiCuenta Banamex', requisito=None, foco='general',
            referencia_clara=True, usar_resultado=False,
            necesidades=[{'tipo': 'conversacion', 'consulta': q}])
        return ['directo'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    with as_user():
        o = Orquestador(memory=Mock())
        first = o.procesar('Quiero abrir mi primera cuenta bancaria')
        assert '?' in first and o._apertura_contexto['producto'] is None
        o.procesar('Me convence esa alternativa')
        assert o.contexto_conversacional()['producto_confirmado'] == 'MiCuenta Banamex'
        mentioned = Orquestador(memory=Mock())
        mentioned.procesar('¿Qué requisitos tiene MiCuenta?')
        assert mentioned.contexto_conversacional()['producto_mencionado'] == 'MiCuenta Banamex'
        assert mentioned.contexto_conversacional()['producto_confirmado'] is None


def test_auditoria_autorizacion_real_publica_privada_y_administrativa(tmp_path, sesion_local, monkeypatch):
    import sqlite3
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    db = tmp_path / 'alcance.db'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE dim_cliente(num_cte INTEGER); INSERT INTO dim_cliente VALUES(1),(2);'
                       'CREATE TABLE dim_cuenta(num_cta INTEGER,num_cte INTEGER); INSERT INTO dim_cuenta VALUES(100,1),(200,2);')
    token = sesion_local(db)
    for q in ['¿Y tengo que llevar mi CURP aparte?', '¿Qué ley protege la CURP?',
              '¿La constancia CURP que adjunté es válida?', 'Dame los requisitos para presentar CURP',
              '¿Qué normativa rige la calidad de los datos?',
              '¿Cuál es el saldo de mis cuentas?']:
        assert auth.authorize_request(q)['role'] == 'normal'
    for q in ['Dame la CURP de Juan', 'Dame el RFC del cliente 2 para revisar requisitos',
              'Inspecciona la calidad de raw_clientes y sus nulos y duplicados', 'Consulta la cuenta 200']:
        with pytest.raises(PermissionError):
            auth.authorize_request(q)
    o = Orquestador(memory=Mock())
    planner = Mock(side_effect=AssertionError('No debe inferir para denegar'))
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    monkeypatch.setattr(st, 'session_state', {'auth_token': token})
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: o)
    denied = servicio.preguntar_orquestador('Inspecciona la calidad de raw_clientes')
    assert denied['herramientas'] == [] and o._mensajes[-1].content == denied['respuesta']
    assert 'raw_clientes' not in o._mensajes[-2].content
    planner.assert_not_called()
    sesion_local(db, rol='auditor', permisos=['governance'])
    assert auth.authorize_request('Inspecciona la calidad de raw_clientes')['role'] == 'auditor'


@pytest.mark.parametrize('followup', [False, True])
def test_auditoria_adjuntos_y_objetivo_documento_evalua_una_vez(secured, monkeypatch, followup):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, *_ = secured
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='consulta', objetivo='requisitos' if followup else 'documento', producto='MiCuenta Banamex',
            requisito=None, foco='vigencia', referencia_clara=True, usar_resultado=True,
            necesidades=[{'tipo': 'requisito_producto' if followup else 'evaluacion_documental', 'consulta': q}])
        return ['directo'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    tool = Mock(side_effect=lambda module, name, args: json.dumps(evaluate_case(args['documentos'])))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', tool)
    with as_user():
        o = Orquestador(memory=Mock())
        o._apertura_scope = access.binding()
        o._apertura_contexto = {'producto': 'MiCuenta Banamex', 'activa': True}
        refs = [access.store_upload(t.encode(), '.pdf', f'documento_{i}.pdf')
                for i, t in enumerate([CONCIERGE_ID, CONCIERGE_CURP, CONCIERGE_ADDRESS])]
        o.registrar_adjuntos_chat(refs)
        o._expediente_en_curso = followup
        result = o.procesar('¿Qué documentos me faltan?' if followup else
                           'Comprueba si estos documentos cumplen los requisitos de la cuenta',
                           documentos_adjuntos=None if followup else refs)
        assert o.ultimo_estado_grafo['expediente']['code'] == 'OK'
        assert o._mensajes[-1].content == result
        tool.assert_called_once_with('proyecto.mcp_servers.document_intelligence_server',
                                     'evaluar_expediente_micuenta', {'documentos': refs})


def test_auditoria_manual_exige_fuente_y_no_acepta_otra(secured, monkeypatch, tmp_path):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.agents import local_documents
    _, as_user, *_ = secured
    (tmp_path / 'Procedimiento operativo.txt').write_text('Contenido sintético', encoding='utf-8')
    monkeypatch.setattr(local_documents, 'ROOT', tmp_path)
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='consulta', objetivo='otro', producto=None, requisito=None,
            foco='general', referencia_clara=False, usar_resultado=False,
            necesidades=[{'tipo': 'conversacion', 'consulta': q}])
        return ['directo'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    with as_user():
        o = Orquestador(memory=Mock())
        reading = o.interpretar_turno('Resume la atención según Procedimiento operativo')
        assert reading['plan'][0] == ['normatividad_rag']
        assert reading['fuentes'][0]['documentos'] == ['Procedimiento operativo.txt']
        o._ultimo_agente = 'normatividad_rag'
        assert o.interpretar_turno('Explica más ese apartado')['plan'][0] == ['normatividad_rag']
    tool = Mock(return_value=('FUENTE: Otro documento.txt\nTexto de otra fuente', {}, 'ok'))
    monkeypatch.setattr(conversational_graph, '_ejecutar_mcp', tool)
    state = {'consulta': 'Resumen', 'plan_conversacional': {'fuentes': reading['fuentes']}}
    out = conversational_graph._nodo_normatividad(state)
    assert 'Sin evidencia suficiente del documento solicitado' in out['resultados']['normatividad_rag']['respuesta']
    tool.assert_called_once()


def test_auditoria_seguimiento_con_fuentes_no_pierde_documento():
    state = {'consulta': 'Explica más ese apartado', 'plan_conversacional': {'fuentes': [
        {'tipo': 'fundamento_normativo', 'consulta': 'Explica más ese apartado'}]}, 'historial': [
        {'role': 'user', 'content': 'Resume la validación según Manual de prueba'},
        {'role': 'assistant', 'content': 'Resumen.\nReferencias:\n- Manual de prueba.pdf, página 2'}]}
    query = conversational_graph._subpregunta_normativa(state)
    assert 'Manual de prueba' in query and 'página 2' not in query
    assert 'Seguimiento:' not in query and 'Referencias:' not in query


@pytest.mark.parametrize('multiple', [False, True])
def test_auditoria_seguimiento_manual_con_aclaracion_del_modelo(secured, monkeypatch, tmp_path, multiple):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.agents import local_documents
    _, as_user, *_ = secured
    names = ['Manual operativo.txt', 'Procedimiento de control.txt'] if multiple else ['Manual operativo.txt']
    for name in names:
        (tmp_path / name).write_text('Contenido sintético', encoding='utf-8')
    monkeypatch.setattr(local_documents, 'ROOT', tmp_path)
    def planner(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='aclaracion', objetivo='aclarar', producto=None, requisito=None,
            foco='general', referencia_clara=False, usar_resultado=False,
            necesidades=[{'tipo': 'aclaracion', 'consulta': 'El modelo no identifica el apartado.'}])
        return ['directo'], [], ''
    model = Mock(side_effect=planner)
    monkeypatch.setattr(conversational_graph, '_plan_llm', model)
    with as_user():
        o = Orquestador(memory=Mock())
        o._ultimo_agente = 'normatividad_rag'
        o.registrar_intercambio('Resume el procedimiento consultado',
                               'Resumen.\nReferencias:\n' + '\n'.join('- '+name for name in names))
        reading = o.interpretar_turno('Explica con más detalle ese apartado')
        if multiple:
            assert reading['plan'][0] == ['directo']
            assert reading['fuentes'][0]['tipo'] == 'aclaracion'
        else:
            assert reading['plan'][0] == ['normatividad_rag']
            assert reading['fuentes'][0]['documentos'] == names
            assert reading['turno']['referencia_clara'] is True
    model.assert_called_once()


@pytest.mark.parametrize('role', ['normal', 'auditor'])
def test_documental_chat_roles_historial_y_seccion_con_permisos(secured, monkeypatch, role):
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.mcp_servers import mcp_client
    tokens, as_user, _, _, _, factory = secured
    data = auth._read()
    data['users']['usuario_a'].update(role=role, permissions=['governance'] if role == 'auditor' else [])
    auth._write(data)
    o = Orquestador(memory=Mock())
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: o)
    calls = []
    def mcp(module, tool, args):
        calls.append(tool)
        return json.dumps(getattr(server, tool)(**args))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)  # transporte simulado; backend y autorización reales
    with as_user():
        ref = access.store_upload(b'# Movimientos\nCuenta: 100\nSaldo final: 9100.00', '.pdf', 'documento_sintetico.pdf')
        index = server.preparar_documento_consultable(ref, '')
        state = {'auth_token': tokens['usuario_a'], 'documento_consultable': {'owner': dq.session_owner(), 'index': index, 'previous': ''}}
        monkeypatch.setattr(st, 'session_state', state)
        result = servicio._preguntar_documento('¿Cuál es el saldo final?')
        assert result['code'] == 'OK' and 'El saldo al cierre' in result['respuesta']
        assert 'Movimientos' in state['documento_consultable']['previous']
        assert 'similarity' not in result['respuesta']
        factory.return_value.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'completo', 'section_id': 'S1'})
        whole = servicio._preguntar_documento('Muéstrame el texto completo de esa sección')
        assert '# Movimientos\nCuenta: 100\nSaldo final: 9100.00' in whole['respuesta']
        assert o._mensajes[-1].content == whole['respuesta']
    factory.reset_mock()
    state['auth_token'] = tokens['usuario_b']
    denied = servicio._preguntar_documento('¿Y el saldo?')
    assert denied['code'] == 'ACCESO_DENEGADO' and '9100.00' not in denied['respuesta']
    assert calls == ['responder_pregunta_documental', 'responder_pregunta_documental']
    factory.assert_not_called()


def test_documental_procedencia_censurada_esta_ligada_al_contexto(secured):
    _, as_user, _, _, _, factory = secured
    with as_user():
        ref = access.store_upload(b'# Datos\nNombre del titular: Persona sintetica\nSaldo final: 9100.00', '.pdf', 'protegido.pdf')
        index = server.preparar_documento_consultable(ref, '')
        assert index['pages'][0]['redacted'] is True
        assert not dq._secciones_consultables(index)[0]['complete']
        changed = copy.deepcopy(index)
        changed['pages'][0]['redacted'] = False
        assert server.responder_pregunta_documental('Texto completo de la sección Datos', changed)['code'] == 'ACCESO_DENEGADO'
    factory.assert_not_called()


@pytest.fixture
def secured(tmp_path, monkeypatch):
    # Los casos anteriores ejercitan rutas deterministas; los de planificación
    # contextual restauran explícitamente la función con un doble del modelo.
    monkeypatch.setattr(conversational_graph, '_plan_llm', lambda *a, **kw: None)
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
    model.invoke.return_value.content = json.dumps({'answerable': True, 'mode': 'respuesta',
        'answer': 'El saldo al cierre es de 9100.00.', 'quotes': [{'id': 'F1', 'text': 'Saldo final: 9100.00'}]})
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
        rows = orchestrator.ultimo_estado_grafo['expediente']['requisitos']
        assert any(r['estado'] == ('NO_VERIFICABLE' if indeterminate else 'VALIDO_SEGUN_REGLAS') for r in rows)
        assert orchestrator._mensajes[-1].content == text
        follow = orchestrator.procesar('¿Qué documentos faltan?')
        assert calls == [refs, refs]
        assert orchestrator._mensajes[-1].content == follow
        assert orchestrator.agentes_invocados == ['document_intelligence']
    with as_user('usuario_b'):
        with pytest.raises(PermissionError):
            orchestrator.procesar('¿Mi expediente MiCuenta está completo?')
    assert len(calls) == 2


@pytest.mark.parametrize('reply', ['la segunda', 'ine_vencida.pdf'])
def test_seleccion_version_documental(secured, monkeypatch, reply):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='sin-modelo', memory=Mock())
    calls = []
    def mcp(module, name, args):
        assert name == 'evaluar_expediente_micuenta'
        calls.append(list(args['documentos']))
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)  # solo transporte/extracción simulados
    with as_user():
        refs = [access.store_upload(text.encode(), '.pdf', name) for text, name in [
            (CONCIERGE_ID, 'ine_vigente.pdf'),
            (CONCIERGE_ID.replace('2027-11-24', '2020-01-01'), 'ine_vencida.pdf'),
            (CONCIERGE_CURP, 'curp.pdf'), (CONCIERGE_ADDRESS, 'domicilio.pdf')]]
        orchestrator.registrar_adjuntos_chat(refs)
        text = orchestrator.procesar('Mis documentos cumplen los requisitos de MiCuenta?')
        assert '1. ine_vigente.pdf' in text and '2. ine_vencida.pdf' in text
        assert all(ref not in text for ref in refs)
        unclear = orchestrator.procesar('la primera o la segunda')
        assert 'única opción' in unclear and len(calls) == 1
        result = orchestrator.procesar(reply)
        assert calls[-1] == refs[1:] and len(calls) == 2
        assert 'vencida' in result
        assert orchestrator.ultimo_estado_grafo['expediente']['requisitos'][0]['incidencias'][0]['estado'] == 'VENCIDO'
        assert orchestrator._adjuntos_chat.keys() == dict.fromkeys(refs).keys()
        for ref in refs:
            access.authorize_file(ref)  # ninguna versión se borró
        orchestrator.reiniciar()
        assert not orchestrator._expediente_selecciones


def test_seleccion_version_rechaza_otra_sesion(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='sin-modelo', memory=Mock())
    calls = []
    def mcp(module, name, args):
        calls.append(args['documentos'])
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)
    with as_user('usuario_b'):
        foreign = access.store_upload(CONCIERGE_ID.encode(), '.pdf', 'ine_ajena.pdf')
    with as_user():
        refs = [access.store_upload(CONCIERGE_ID.encode(), '.pdf', n) for n in ['a.pdf', 'b.pdf']]
        orchestrator.registrar_adjuntos_chat(refs)
        orchestrator.procesar('Evalua documentos para abrir MiCuenta')
        with pytest.raises(PermissionError):
            orchestrator.procesar(foreign)
        assert len(calls) == 1 and orchestrator._aclaracion_pendiente['tipo'] == 'version_documental'


def test_contextual_mimo_interpreta_confirmacion_correccion_y_plan_compartido(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    captured = []
    turns = iter([
        ('consulta', 'apertura', None, False, ['directo']),
        ('confirmacion', 'requisitos', 'MiCuenta Banamex', True, ['directo']),
        ('correccion', 'apertura', None, False, ['directo']),
        ('cambio_tema', 'datos', None, True, ['data_analyst', 'normatividad_rag']),
    ])
    class Model:
        def __init__(self, **kwargs):
            assert kwargs['format']['properties']['turno']['additionalProperties'] is False
        def invoke(self, messages):
            captured.append(copy.deepcopy(messages))
            act, goal, product, clear, plan = next(turns)
            return Mock(content=json.dumps({'plan': plan, 'subpreguntas': [], 'turno': {
                'acto': act, 'objetivo': goal, 'producto': product, 'referencia_clara': clear,
                'requisito': None, 'usar_resultado': False, 'foco': 'general'},
                'necesidades': ([{'tipo': 'datos', 'consulta': 'Mis movimientos'},
                                 {'tipo': 'fundamento_normativo', 'consulta': 'Obligaciones aplicables'}]
                                if goal == 'datos' else [{'tipo': 'requisito_producto' if product else 'conversacion',
                                                         'consulta': messages[-1]['content']}])}))
    monkeypatch.setattr(conversational_graph, '_plan_llm', REAL_CONVERSATIONAL_PLAN)
    monkeypatch.setattr(conversational_graph, 'ChatOllama', Model)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    o = Orquestador(memory=Mock())
    with as_user():
        o.procesar('Busco empezar mi relación bancaria con ustedes')
        assert o._apertura_contexto['producto'] is None
        o.procesar('Me convence la alternativa que acabas de ofrecer')
        assert o._apertura_contexto['producto'] == 'MiCuenta Banamex'
        o.procesar('Rectifico: necesito otro producto, todavía no sé cuál')
        assert o._apertura_contexto['producto'] is None
        def graph(query, **kwargs):
            assert kwargs['plan_conversacional']['rutas'] == ['data_analyst', 'normatividad_rag']
            assert kwargs['planificacion_intentada'] is True
            return {'respuesta': 'Resultados de prueba de dos fuentes', 'trazas': []}
        monkeypatch.setattr(conversational_graph, 'ejecutar_grafo', graph)
        o.procesar('Compara mis movimientos y explica las obligaciones aplicables')
    assert len(captured) == 4
    assert captured[1][-1]['content'] == 'Me convence la alternativa que acabas de ofrecer'
    assert captured[1][0]['content'].count('Busco empezar mi relación bancaria con ustedes') == 1
    assert 'MiCuenta Banamex' in captured[1][0]['content']
    assert o.ultimo_diagnostico_plan == 'llm_contextual'


@pytest.mark.parametrize('query', [
    'Para la cuenta que elegimos, ¿en qué supuesto exigen la constancia por separado?',
    '¿Y tengo que llevar mi CURP aparte?',
])
def test_fuente_producto_prevalece_sobre_plan_rag(secured, monkeypatch, query):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.agents.document_requirements import requirements_matrix
    from proyecto.mcp_servers import mcp_client
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    captured = []
    def plan(q, *args, lectura_turno, **kwargs):
        captured.append(kwargs['contexto_conversacional'])
        lectura_turno.update(acto='seguimiento', objetivo='normativa', producto='MiCuenta Banamex',
            requisito='curp', referencia_clara=True, usar_resultado=False, foco='vigencia',
            necesidades=[{'tipo': 'requisito_producto', 'consulta': q}])
        return ['normatividad_rag'], [], ''  # Propuesta incorrecta: debe corregirla el servidor.
    monkeypatch.setattr(conversational_graph, '_plan_llm', plan)
    transport = Mock(side_effect=AssertionError('No corresponde consultar MCP/RAG'))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', transport)
    def writer(_, messages):
        assert 'si la CURP no figura en la INE' in messages[0]['content']
        assert 'esta respuesta no acredita haber revisado una INE' in messages[0]['content']
        return 'La constancia CURP separada solo se requiere si no figura en tu INE.'
    monkeypatch.setattr(style, '_llamar_ollama', writer)
    with as_user():
        o._apertura_scope = access.binding()
        o._apertura_contexto = {'activa': True, 'producto': 'MiCuenta Banamex'}
        answer = o.procesar(query)
        assert o.ultima_seleccion_fuentes == ['requisito_producto']
        assert o.ultimo_estado_grafo['fuente_estructurada']['id'] == requirements_matrix()['id']
        assert o.ultimas_herramientas == [] and o._mensajes[-1].content == answer
        assert 'si no figura en tu INE' in answer
    assert captured[0]['producto_confirmado'] == 'MiCuenta Banamex'
    transport.assert_not_called()


@pytest.mark.parametrize('kind,clear,product,query', [
    ('fundamento_normativo', True, 'MiCuenta Banamex', 'En lo del registro poblacional, ¿qué condición obliga a presentarlo aparte?'),
    ('requisito_producto', False, 'MiCuenta Banamex', '¿Eso se exige en todos los casos?'),
    ('requisito_producto', True, None, 'Cambio de idea, ahora quiero otra cuenta: ¿qué exigen?'),
])
def test_fuente_ambigua_o_cambio_producto_no_asume_matriz(secured, monkeypatch, kind, clear, product, query):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    def plan(q, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='seguimiento' if product else 'correccion', objetivo='normativa',
            producto=product, requisito='curp', referencia_clara=clear, usar_resultado=False, foco='general',
            necesidades=[{'tipo': kind, 'consulta': q}])
        return ['normatividad_rag'], [], ''
    monkeypatch.setattr(conversational_graph, '_plan_llm', plan)
    monkeypatch.setattr(style, '_llamar_ollama', lambda *_: '¿Te refieres a un requisito de la cuenta o al fundamento legal?')
    with as_user():
        o = Orquestador(memory=Mock())
        o._apertura_scope = access.binding()
        o._apertura_contexto = {'activa': True, 'producto': 'MiCuenta Banamex'}
        o.procesar(query)
        assert o.ultima_seleccion_fuentes == ['aclaracion']
        assert o.ultimas_herramientas == []
        assert 'fuente_estructurada' not in o.ultimo_estado_grafo


def test_fuente_normativa_curp_y_combinada_consultan_solo_contexto_legal(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    normative_query = '¿Qué ley regula la protección de la CURP?'
    calls = []
    def mcp(module, tool, args):
        calls.append((module, tool, args))
        return 'FUENTE: documento_normativo_sintetico\nLa evidencia normativa de prueba protege los datos personales.'
    monkeypatch.setattr(conversational_graph, '_llamar_mcp', mcp)
    monkeypatch.setattr(style, '_llamar_ollama', lambda *_: 'Respuesta de prueba basada en evidencia normativa.')
    synthesis = Mock(side_effect=lambda state, plan, results: (
        'La CURP separada se requiere si no figura en la INE. Contexto normativo de prueba.'
        if results['normatividad_rag'].get('fuente_estructurada') else None))
    monkeypatch.setattr(conversational_graph, '_sintetizar_cooperativo', synthesis)
    for combined in [False, True]:
        def plan(q, *args, lectura_turno, **kwargs):
            needs = [{'tipo': 'fundamento_normativo', 'consulta': normative_query}]
            if combined:
                needs.insert(0, {'tipo': 'requisito_producto', 'consulta': 'Condición de presentación de CURP para MiCuenta'})
            lectura_turno.update(acto='seguimiento', objetivo='normativa', producto='MiCuenta Banamex',
                requisito='curp', referencia_clara=True, usar_resultado=False, foco='general', necesidades=needs)
            return ['normatividad_rag'], [], ''
        monkeypatch.setattr(conversational_graph, '_plan_llm', plan)
        with as_user():
            o = Orquestador(memory=Mock())
            o._document_scope = access.binding()  # Historial local; no memoria externa en esta prueba.
            o._apertura_scope = access.binding()
            o._apertura_contexto = {'activa': True, 'producto': 'MiCuenta Banamex'}
            o.procesar(normative_query if not combined else '¿Debo presentar CURP para esa cuenta y qué ley protege ese dato?')
            result = o.ultimo_estado_grafo['resultados']['normatividad_rag']
            assert bool(result.get('fuente_estructurada')) is combined
            assert o.ultimo_estado_grafo['plan'] == ['normatividad_rag']
    assert len(calls) == 2  # Una recuperación por turno, ninguna consulta de requisitos enviada al RAG.
    assert all(c[1] == 'buscar_normatividad' and c[2] == {'query': normative_query} for c in calls)
    assert synthesis.call_args.args[2]['normatividad_rag']['fuente_estructurada']['producto'] == 'MiCuenta Banamex'


def test_fuente_producto_se_conserva_si_complemento_normativo_falla(monkeypatch):
    calls = Mock(return_value=('Error técnico simulado', {'name': 'mcp_buscar_normatividad', 'estado': 'error'}, 'error'))
    monkeypatch.setattr(conversational_graph, '_ejecutar_mcp', calls)
    state = {'plan_conversacional': {'fuentes': [
        {'tipo': 'requisito_producto', 'id': 'matriz_producto', 'consulta': 'Requisitos del producto'},
        {'tipo': 'fundamento_normativo', 'consulta': 'Fundamento legal complementario'}]}}
    output = conversational_graph._nodo_normatividad(state)
    result = output['resultados']['normatividad_rag']
    assert result['fuente_estructurada']['producto'] == 'MiCuenta Banamex'
    assert result['estado'] == 'ok' and result['contexto_normativo_estado'] == 'error'
    assert 'no inferir su contenido' in result['respuesta']
    calls.assert_called_once()
    checked = conversational_graph._nodo_verificar({'plan': ['normatividad_rag'], 'resultados': output['resultados']})
    assert checked['reintento'] is False


def test_fuente_documental_curp_usa_evaluacion_autorizada(secured, monkeypatch):
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    tokens, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a']})
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: o)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    calls = []
    def mcp(module, tool, args):
        assert tool == 'evaluar_expediente_micuenta'
        calls.append(args['documentos'])
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)
    with as_user():
        ref = access.store_upload(CONCIERGE_CURP.encode(), '.pdf', 'constancia.pdf')
        o.registrar_adjuntos_chat([ref])
        result = servicio.preguntar_orquestador('¿La constancia CURP que adjunté es válida?')
        assert result['evaluacion_documental']['requisito'] == 'curp'
        assert result['herramientas'] == ['evaluar_expediente_micuenta'] and calls == [[ref]]


def test_contextual_documentos_proyeccion_sin_texto_ni_referencias_privadas(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    with as_user():
        ref = access.store_upload(CONCIERGE_ID.encode(), '.pdf', 'identificacion.pdf')
        o.registrar_adjuntos_chat([ref])
        o._documentos = [{'texto': 'NO ENVIAR CONTENIDO COMPLETO'}]
        o._expediente_selecciones = {'ine': {'elegida': ref, 'candidatos': [ref]}}
        o._aclaracion_pendiente = {'tipo': 'version_documental', 'requisito': 'ine', 'documentos': [ref], 'binding': access.binding()}
        context = o.contexto_conversacional()
        assert context['selecciones'] == {'ine': 'D1'}
        assert context['aclaracion_pendiente']['opciones'] == ['D1']
        assert ref not in json.dumps(context) and 'NO ENVIAR CONTENIDO' not in json.dumps(context)
        assert context['resultado_es_actual'] is False
    with as_user('usuario_b'), pytest.raises(PermissionError):
        o.contexto_conversacional()


@pytest.mark.parametrize('candidate,expected', [
    ('Tu identificación no está vigente.', True),
    ('Tu identificación está vigente.', False),
    ('Tu identificación no está vencida.', False),
])
def test_contextual_equivalencias_redactor(candidate, expected):
    from proyecto.orchestration.perfil_respuesta import _redaccion_documental_coherente
    result = {'concierge': {'requisitos': [{'requisito': 'ine', 'estado': 'PRESENTE', 'incidencias': [{'estado': 'VENCIDO'}]}]}}
    assert _redaccion_documental_coherente(candidate, 'Tu identificación está vencida.', result) is expected


def test_contextual_fechas_opciones_y_diagnostico():
    from proyecto.orchestration.perfil_respuesta import redactar_respuesta, detectar_perfil
    diagnostic = {}
    def run(source, target):
        return redactar_respuesta('Consulta de prueba', source, detectar_perfil('normal', ''), {}, 'doble',
                                  llamar_modelo=lambda *_: target, hechos_protegidos=[source], diagnostico=diagnostic)
    assert run('La fecha es 2028-12-31.', 'La fecha es 31/12/2028.') == 'La fecha es 31/12/2028.'
    assert diagnostic['origen'] == 'modelo'
    assert run('La fecha es 2028-12-31.', 'La fecha es 30/12/2028.') == 'La fecha es 2028-12-31.'
    assert diagnostic == {'origen': 'respaldo', 'motivo': 'guarda_hechos_o_seguridad'}
    assert run('1. uno.pdf\n2. dos.pdf', '1. `uno.pdf`\n2. `dos.pdf`') == '1. `uno.pdf`\n2. `dos.pdf`'


def test_contextual_plan_invalido_no_repite_inferencia_y_denegacion_previa(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    planner = Mock(return_value=None)
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    o = Orquestador(memory=Mock())
    with as_user():
        with pytest.raises(PermissionError):
            o.procesar('Consulta de cuenta 200')
        planner.assert_not_called()
        def graph(query, **kwargs):
            assert kwargs['planificacion_intentada'] is True
            assert kwargs['plan_conversacional'] is None
            return {'respuesta': 'Ruta de respaldo de prueba', 'trazas': []}
        monkeypatch.setattr(conversational_graph, 'ejecutar_grafo', graph)
        o.procesar('Mis movimientos y normativa aplicable')
    planner.assert_called_once()
    assert o.ultimo_diagnostico_plan == 'respaldo_plan_invalido'


def test_contextual_servicio_reutiliza_plan_y_ruta_documental(secured, monkeypatch):
    import streamlit as st
    from proyecto import servicio
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    tokens, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    monkeypatch.setattr(servicio, '_get_orquestador', lambda: o)
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a']})
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    def plan(query, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='seguimiento', objetivo='documento', producto='MiCuenta Banamex',
                             requisito='ine', usar_resultado=False, referencia_clara=True, foco='vigencia')
        return ['directo'], [], ''
    planner = Mock(side_effect=plan)
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    tools = Mock(side_effect=lambda module, name, args: json.dumps(evaluate_case(args['documentos'])))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', tools)
    with as_user():
        ref = access.store_upload(CONCIERGE_ID.encode(), '.pdf', 'credencial.pdf')
        o.registrar_adjuntos_chat([ref])
        reply = servicio.preguntar_orquestador('La fecha que figura ahí, ¿sirve para ese trámite?')
        assert reply['code'] == 'VALIDO_SEGUN_REGLAS'
        assert reply['herramientas'] == ['evaluar_expediente_micuenta']
        assert o._mensajes[-1].content == reply['respuesta']
    planner.assert_called_once()
    tools.assert_called_once()
    assert tools.call_args.args[2] == {'documentos': [ref]}


def test_contextual_admin_y_ambiguedad_no_seleccionan_documentos(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    def plan(query, *args, lectura_turno, **kwargs):
        lectura_turno.update(acto='aclaracion', objetivo='aclarar', producto=None, requisito=None,
                             usar_resultado=False, referencia_clara=False, foco='general')
        return ['directo'], [], ''
    planner = Mock(side_effect=plan)
    monkeypatch.setattr(conversational_graph, '_plan_llm', planner)
    monkeypatch.setattr(style, '_llamar_ollama', lambda _, m: m[0]['content'].split('Hechos autorizados:\n')[1].split('\nContexto autorizado')[0])
    with as_user():
        refs = [access.store_upload(CONCIERGE_ID.encode(), '.pdf', name) for name in ['una.pdf', 'otra.pdf']]
        o.registrar_adjuntos_chat(refs)
        o._aclaracion_pendiente = {'tipo': 'version_documental', 'requisito': 'ine', 'documentos': refs, 'binding': access.binding()}
        answer = o.procesar('No me queda claro con cuál deberíamos continuar')
        assert '1. una.pdf' in answer and '2. otra.pdf' in answer
        assert not getattr(o, '_expediente_selecciones', {})
        assert o.ultimas_herramientas == []
    planner.reset_mock()
    data = auth._read()
    data['users']['usuario_b'].update(role='auditor', permissions=['governance'])
    auth._write(data)
    with as_user('usuario_b'):
        admin = Orquestador(memory=Mock())
        route = Mock(return_value='Agregados de prueba')
        monkeypatch.setattr(admin, '_procesar_grafo', route)
        admin.procesar('Revisa nulos y duplicados de la base')
        assert route.call_args.kwargs['perfil']['rol'] == 'superusuario'
        assert admin.ultimo_perfil_respuesta is not None
    planner.assert_not_called()


def test_contextual_aclaracion_no_se_sustituye_por_requisito_inventado():
    from proyecto.orchestration.perfil_respuesta import redactar_respuesta, detectar_perfil
    question = '¿A qué documento te refieres?'
    diagnostic = {}
    response = redactar_respuesta('Consulta ambigua', question, detectar_perfil('normal', ''), {}, 'doble',
        llamar_modelo=lambda *_: 'La constancia de ingresos se entrega por separado.',
        hechos_protegidos=[question], contexto={'interpretacion_turno': {'objetivo': 'aclarar'}}, diagnostico=diagnostic)
    assert response == question
    assert diagnostic == {'origen': 'respaldo', 'motivo': 'aclaracion_sin_pregunta'}


def test_contextual_revocacion_durante_plan_no_redacta(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    _, as_user, *_ = secured
    writer = Mock()
    monkeypatch.setattr(style, '_llamar_ollama', writer)
    def revoke(*args, **kwargs):
        data = auth._read()
        data['users']['usuario_a']['permissions'] = ['aggregate']
        auth._write(data)
        return None
    monkeypatch.setattr(conversational_graph, '_plan_llm', revoke)
    with as_user(), pytest.raises(PermissionError):
        Orquestador(memory=Mock()).procesar('Quiero orientacion bancaria')
    writer.assert_not_called()


def test_contextual_producto_no_documentado_falla_cerrado(secured, monkeypatch, caplog):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    class Model:
        def __init__(self, **kwargs): pass
        def invoke(self, messages):
            return Mock(content=json.dumps({'plan': ['directo'], 'turno': {
                'acto': 'consulta', 'objetivo': 'requisitos', 'producto': 'Producto inventado',
                'requisito': None, 'usar_resultado': False, 'referencia_clara': True, 'foco': 'requisitos'}}))
    monkeypatch.setattr(conversational_graph, '_plan_llm', REAL_CONVERSATIONAL_PLAN)
    monkeypatch.setattr(conversational_graph, 'ChatOllama', Model)
    with as_user():
        reading = Orquestador(memory=Mock()).interpretar_turno('Una consulta de prueba sin datos privados')
    assert reading['plan'] is None and reading['turno'] == {}
    assert 'producto_no_documentado' in caplog.text
    assert 'Una consulta de prueba sin datos privados' not in caplog.text


def test_contextual_enum_no_confunde_mayusculas_con_tipo_desconocido(monkeypatch):
    class Model:
        def __init__(self, **kwargs): pass
        def invoke(self, messages):
            assert 'incluye siempre turno' in messages[0]['content']
            return Mock(content=json.dumps({'plan': ['directo'], 'turno': {
                'acto': 'ACLARACION', 'objetivo': 'aclarar', 'producto': 'micuenta banamex',
                'requisito': 'INE', 'usar_resultado': False, 'referencia_clara': True, 'foco': 'requisitos'},
                'necesidades': [{'tipo': 'aclaracion', 'consulta': 'Pregunta sintética'}]}))
    monkeypatch.setattr(conversational_graph, 'ChatOllama', Model)
    reading = {}
    assert REAL_CONVERSATIONAL_PLAN('Pregunta sintética', {}, 'doble',
        contexto_conversacional={'producto_documentado': 'MiCuenta Banamex'}, lectura_turno=reading)
    assert reading['requisito'] == 'ine' and reading['acto'] == 'aclaracion'
    assert reading['producto'] == 'MiCuenta Banamex'


def test_flujo_conversacional_producto_y_payload(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.config.models import modelo_de
    _, as_user, *_ = secured
    captured = []
    def llm(model, messages):
        captured.append((model, copy.deepcopy(messages)))
        if len(captured) == 1:
            return '¡Hola! Puedo orientarte con MiCuenta de Banamex. ¿Te interesa esa cuenta?'
        return 'Para MiCuenta necesitas identificación INE vigente y comprobante de domicilio. La CURP separada es condicional; el RFC depende de la actividad empresarial.'
    monkeypatch.setattr(style, '_llamar_ollama', llm)
    o = Orquestador(memory=Mock())
    with as_user():
        first = o.procesar('Quiero abrir una cuenta')
        assert o._apertura_contexto['producto'] is None
        second = o.procesar('Sí, justo esa')
    assert len(captured) == 2 and all(model == modelo_de('orchestrator') for model, _ in captured)
    assert captured[-1][1][-1] == {'role': 'user', 'content': 'Sí, justo esa'}
    assert sum(m['content'] == 'Sí, justo esa' for m in captured[-1][1]) == 1
    assert any(m['content'] == first for m in captured[-1][1])
    assert o._apertura_contexto['producto'] == 'MiCuenta Banamex'
    assert first == '¡Hola! Puedo orientarte con MiCuenta de Banamex. ¿Te interesa esa cuenta?'
    assert second.startswith('Para MiCuenta necesitas identificación INE vigente')
    assert o._mensajes[-1].content == second and 'identificación' in second


def test_flujo_conversacional_documentos_seleccion_y_tema(secured, monkeypatch):
    import streamlit as st
    import proyecto.servicio as service
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    tokens, as_user, *_ = secured
    model_calls, tool_calls, backend_results = [], [], []
    def llm(model, messages):
        model_calls.append(copy.deepcopy(messages))
        return messages[0]['content'].split('Hechos autorizados:\n', 1)[1]
    def mcp(module, name, args):
        tool_calls.append(list(args['documentos']))
        result = evaluate_case(args['documentos'])
        backend_results.append(copy.deepcopy(result))
        return json.dumps(result)
    monkeypatch.setattr(style, '_llamar_ollama', llm)
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)
    o = Orquestador(memory=Mock())
    monkeypatch.setattr(service, '_get_orquestador', lambda: o)
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a']})
    with as_user():
        o.procesar('MiCuenta')
        refs = [access.store_upload(text.encode(), '.pdf', name) for text, name in [
            (CONCIERGE_ID, 'primera.pdf'), (CONCIERGE_ID.replace('2027-11-24', '2020-01-01'), 'segunda.pdf'),
            (CONCIERGE_CURP, 'curp.pdf'), (CONCIERGE_ADDRESS, 'domicilio.pdf')]]
        o.registrar_adjuntos_chat(refs)
        o.procesar('Ya te envié mis documentos')
        assert len(tool_calls) == 1 and o._aclaracion_pendiente['tipo'] == 'version_documental'
        pending = copy.deepcopy(o._aclaracion_pendiente)
        graph = Mock(return_value='Ruta bancaria simulada; sin SQL real')
        monkeypatch.setattr(o, '_procesar_grafo', graph)
        o.procesar('Cual es el saldo de mis cuentas?')
        graph.assert_called_once()
        assert o._aclaracion_pendiente == pending
        o.procesar('La segunda')
        assert tool_calls[-1] == refs[1:] and len(tool_calls) == 2
        response = service.preguntar_orquestador('¿Entonces está vencida?')
        assert response.get('code') == 'PRESENTE'
        assert 'vencida' in response['respuesta'] and tool_calls[-1] == [refs[1]]
        assert o._mensajes[-1].content == response['respuesta']
        assert o._expediente_selecciones['ine']['elegida'] == refs[1]
        assert o._adjuntos_chat.keys() == dict.fromkeys(refs).keys()
        assert len(tool_calls) == 3  # una evaluación por consulta; redactar no reevalúa
        assert len(model_calls) == 4  # producto, expediente, elección y consulta individual
        assert model_calls[-1][-1] == {'role': 'user', 'content': '¿Entonces está vencida?'}
        assert backend_results[0]['requisitos'][0]['estado'] == 'NO_VERIFICABLE'
        assert backend_results[1]['requisitos'][0]['incidencias'][0]['estado'] == 'VENCIDO'


def test_flujo_conversacional_faltantes_sin_repetir(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import perfil_respuesta as style
    from proyecto.mcp_servers import mcp_client
    from proyecto.agents.document_requirements import evaluate_case
    _, as_user, *_ = secured
    calls = []
    monkeypatch.setattr(style, '_llamar_ollama', lambda model, messages: messages[0]['content'].split('Hechos autorizados:\n')[1])
    def mcp(module, name, args):
        calls.append(args['documentos'])
        return json.dumps(evaluate_case(args['documentos']))
    monkeypatch.setattr(mcp_client, 'llamar_herramienta', mcp)
    o = Orquestador(memory=Mock())
    with as_user():
        o.procesar('MiCuenta')
        refs = [access.store_upload(text.encode(), '.pdf', name) for text, name in [(CONCIERGE_ID, 'ine.pdf'), (CONCIERGE_CURP, 'curp.pdf')]]
        o.registrar_adjuntos_chat(refs)
        o.procesar('Ya te envié mis documentos')
        answer = o.procesar('¿Qué me falta?')
    assert 'comprobante de domicilio' in answer and 'identificación' not in answer
    assert 'NO_VERIFICABLE' not in answer and calls == [refs, refs]


def test_flujo_conversacional_turno_actual_con_documentos(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration import grafo
    _, as_user, *_ = secured
    o = Orquestador(memory=Mock())
    captured = []
    def graph(query, **kwargs):
        captured.extend(grafo._normalizar_historial(kwargs['historial']))
        return {'respuesta': 'Respuesta de prueba', 'plan': ['directo']}
    monkeypatch.setattr(grafo, 'ejecutar_grafo', graph)
    with as_user():
        ref = access.store_upload(CONCIERGE_ID.encode(), '.pdf', 'ine.pdf')
        o.registrar_adjuntos_chat([ref])
        o.registrar_intercambio('Pregunta anterior', 'Respuesta anterior')
        o._procesar_grafo('¿Qué información te falta?')
    assert sum(m['content'] == '¿Qué información te falta?' for m in captured) == 1
    messages = []
    class Model:
        def __init__(self, **kwargs): pass
        def invoke(self, payload):
            messages.extend(payload)
            return Mock(content='Prueba sin inferencia')
    monkeypatch.setattr(grafo, 'ChatOllama', Model)
    grafo._respuesta_directa({'consulta': '¿Qué información te falta?', 'historial': captured})
    assert sum(m['content'] == '¿Qué información te falta?' for m in messages) == 1


@pytest.mark.parametrize('candidate', [
    'Tu identificación está vigente.', 'Tu identificación no está vencida.',
    'El expediente está completo.', 'Necesitas una fotografía y referencia bancaria.',
    'La fecha es 2030-01-01.', 'MCP devuelve NO_VERIFICABLE.',
])
def test_flujo_conversacional_redaccion_contradictoria(candidate):
    from proyecto.orchestration.perfil_respuesta import redactar_respuesta, detectar_perfil
    facts = ['Tu identificación está vencida.']
    result = {'concierge': {'requisitos': [{'requisito': 'ine', 'estado': 'PRESENTE', 'incidencias': [{'estado': 'VENCIDO'}]}], 'completo_segun_reglas': False}}
    saved = copy.deepcopy(result)
    answer = redactar_respuesta('¿Está vencida?', facts[0], detectar_perfil('normal', ''), result, 'modelo-prueba',
                               llamar_modelo=lambda *_: candidate, hechos_protegidos=facts)
    assert answer == facts[0] and result == saved


def test_flujo_conversacional_fallo_modelo_y_denegacion(secured, monkeypatch):
    import streamlit as st
    import proyecto.servicio as service
    from proyecto.orchestration import perfil_respuesta as style
    tokens, as_user, *_ = secured
    model = Mock(side_effect=RuntimeError('Fallo simulado'))
    monkeypatch.setattr(style, '_llamar_ollama', model)
    assert style.redactar_respuesta('Consulta', 'Hecho comprobado.', style.detectar_perfil('normal', ''), {}, 'prueba',
                                   hechos_protegidos=['Hecho comprobado.']) == 'Hecho comprobado.'
    with as_user('usuario_b'):
        foreign = access.store_upload(CONCIERGE_ID.encode(), '.pdf', 'ajena.pdf')
    model.reset_mock()
    monkeypatch.setattr(st, 'session_state', {'auth_token': tokens['usuario_a']})
    with as_user():
        denied = service.procesar_documento(foreign, '¿Mi INE está vigente?')
    assert denied['code'] == 'ACCESO_DENEGADO'
    model.assert_not_called()


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
        plan = planear_rutas_hibrido('¿Qué requisitos necesito para abrir MiCuenta Banamex?', rol='normal')
        assert plan[0] == ['normatividad_rag']


@pytest.mark.parametrize('query', [
    '¿Qué necesito para abrir una cuenta?',
    '¿Sabes cuáles son los requisitos para abrir una cuenta?',
    '¿Cómo puedo abrir una cuenta bancaria?',
    '¿Qué documentos necesito para sacar una cuenta?',
    'Quiero abrir una cuenta, ¿qué necesito?',
])
def test_apertura_general_no_se_envia_a_sql(secured, monkeypatch, query):
    from proyecto.orchestration.grafo import intencion_micuenta, planear_rutas, planear_rutas_hibrido
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    graph = Mock(side_effect=AssertionError('Una aclaración de apertura no requiere SQL ni LLM'))
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    assert intencion_micuenta(query) == 'apertura_general'
    assert planear_rutas(query, rol='normal') == ['normatividad_rag']
    assert planear_rutas_hibrido(query, rol='normal')[0] == ['normatividad_rag']
    with as_user():
        text = orchestrator.procesar(query)
    assert orchestrator.ultimo_estado_grafo['interpretacion']['slots']['intencion_apertura'] == 'apertura_general'
    assert orchestrator._apertura_contexto['producto'] is None and '?' in text
    assert orchestrator._mensajes[-1].content == text
    assert not orchestrator.ultimas_herramientas
    graph.assert_not_called()


@pytest.mark.parametrize('query', [
    'hola sabes cuales son los requisito spara abirir una cuenta ?',
    'Me orientas para la apretura de una cuenta bancaria?',
])
def test_apertura_errores_ortograficos(secured, monkeypatch, query):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    graph = Mock(side_effect=AssertionError('La aclaración no debe caer en SQL'))
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    with as_user():
        text = orchestrator.procesar(query)
    assert '?' in text and 'matriz' not in text.casefold()
    assert orchestrator.ultimo_estado_grafo['interpretacion']['slots']['intencion_apertura'] == 'apertura_general'
    assert orchestrator._mensajes[-2].content == query
    assert not orchestrator.ultimas_herramientas
    graph.assert_not_called()


def test_apertura_errores_conserva_saldo_admin_y_mes():
    from proyecto.orchestration.interpretacion import interpretar_consulta
    from proyecto.orchestration.grafo import planear_rutas_hibrido
    assert planear_rutas_hibrido('Cual es el saldo de mis cuentas?', rol='normal')[0] == ['data_analyst']
    assert planear_rutas_hibrido('Analiza calidad de datos y valores nulos', rol='auditor')[0] == ['data_steward']
    result = interpretar_consulta('Puedo consultar mi cuenta en abril?', producto_apertura='MiCuenta Banamex')
    assert not result['slots'].get('intencion_apertura')


def test_apertura_micuenta_matriz_sin_citas_rag_inventadas(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.agents.document_requirements import requirements_matrix
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    monkeypatch.setattr(orchestrator, '_procesar_grafo', Mock(side_effect=AssertionError('No requiere recuperación remota')))
    with as_user():
        text = orchestrator.procesar('¿Sabes cuáles son los requisitos para abrir MiCuenta?')
    matrix = requirements_matrix()
    assert orchestrator.ultimo_estado_grafo['fuente_estructurada']['version'] == matrix['version']
    assert all(s['url'] in text and s['fecha_consulta'] in text for s in matrix['fuentes'])
    assert all(word in text for word in ['INE vigente', 'Comprobante de domicilio', 'CURP separada', 'RFC', 'Condicional', 'no he confirmado'])
    assert 'matriz' not in text.casefold() and 'RAG' not in text
    assert not orchestrator.ultimas_herramientas


def test_apertura_conserva_expediente_saldo_y_seguimiento(secured, monkeypatch):
    from proyecto.orchestration.grafo import intencion_micuenta, planear_rutas
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    case = Mock(return_value='ruta MCP existente')
    graph = Mock(return_value='ruta de saldo existente')
    monkeypatch.setattr(orchestrator, '_evaluar_expediente_chat', case)
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    query = '¿Mis documentos cumplen los requisitos para abrir MiCuenta?'
    with as_user():
        refs = [access.store_upload(TEXT.encode(), '.pdf', name) for name in ['uno.pdf', 'dos.pdf']]
        orchestrator.registrar_adjuntos_chat(refs)
        assert orchestrator.procesar(query) == 'ruta MCP existente'
        assert set(refs) == orchestrator._document_references
        orchestrator._expediente_en_curso = True
        assert orchestrator.procesar('¿Qué documentos me faltan?') == 'ruta MCP existente'
        assert orchestrator.procesar('¿Cuál es mi saldo?') == 'ruta de saldo existente'
    assert case.call_count == 2 and graph.call_count == 1
    for query in ['¿Cuál es mi saldo?', '¿Cuáles fueron mis últimos movimientos?', '¿Qué cuentas tengo?']:
        assert intencion_micuenta(query) is None
        assert 'data_analyst' in planear_rutas(query)


def test_apertura_slots_conversacionales_user_sin_repetir_producto(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    _, as_user, *_ = secured
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    graph = Mock(return_value='ruta previa')
    evaluate = Mock(return_value='evaluación MCP existente')
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    monkeypatch.setattr(orchestrator, '_evaluar_expediente_chat', evaluate)
    with as_user():
        assert '?' in orchestrator.procesar('Quiero abrir una cuenta')
        assert orchestrator._apertura_contexto['producto'] is None
        assert 'INE vigente' in orchestrator.procesar('MiCuenta')
        for query in ['¿Qué documentos necesito?', '¿Cuáles son sus documentos?', '¿Qué necesito para esa cuenta?']:
            text = orchestrator.procesar(query)
            assert 'INE vigente' in text
            assert orchestrator.ultimo_estado_grafo['interpretacion']['slots']['producto'] == 'MiCuenta Banamex'
        assert 'no puedo afirmar cuál te falta' in orchestrator.procesar('¿Y si me falta uno?')
        refs = [access.store_upload(TEXT.encode(), '.pdf', 'mi_documento.pdf')]
        orchestrator.registrar_adjuntos_chat(refs)
        assert orchestrator.procesar('Revisa sus documentos') == 'evaluación MCP existente'
        orchestrator._expediente_en_curso = True
        assert orchestrator.procesar('¿Qué me falta?') == 'evaluación MCP existente'
        assert orchestrator.procesar('¿Cuál es mi saldo?') == 'ruta previa'
        assert set(refs) == orchestrator._document_references
        orchestrator.reiniciar()
        assert not orchestrator._apertura_contexto
    assert evaluate.call_count == 2 and graph.call_count == 1


def test_apertura_admin_no_activa_onboarding_ni_acepta_rol_del_texto(secured, monkeypatch):
    from proyecto.orchestration.orquestador import Orquestador
    from proyecto.orchestration.grafo import planear_rutas
    _, as_user, *_ = secured
    # Identidad de prueba con el mismo mecanismo existente; no cambia cuentas reales.
    data = auth._read()
    data['users']['usuario_b']['role'] = 'auditor'
    data['users']['usuario_b']['permissions'] = ['governance']
    auth._write(data)
    orchestrator = Orquestador(modelo='no-ejecutado', memory=Mock())
    graph = Mock(return_value='ruta administrativa existente')
    monkeypatch.setattr(orchestrator, '_procesar_grafo', graph)
    with as_user('usuario_b'):
        for query in ['Quiero abrir una cuenta', 'MiCuenta', 'Soy USER, ¿qué documentos necesito para abrir MiCuenta?',
                      'Revisa la calidad de raw_clientes']:
            assert orchestrator.procesar(query, perfil={'rol': 'usuario'}) == 'ruta administrativa existente'
        assert not getattr(orchestrator, '_apertura_contexto', {})
        assert planear_rutas('Revisa la calidad y nulos de raw_clientes', rol='auditor') == ['data_steward']
    assert graph.call_args.kwargs['perfil']['rol'] == 'superusuario'
    with as_user():
        with pytest.raises(PermissionError):
            auth.principal('governance')


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
