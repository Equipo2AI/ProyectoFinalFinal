"""Bloqueo de entrada: UI real AppTest, backend controlado sin inferencia."""
import ast
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fallar', [False, True])
def test_entrada_se_bloquea_y_se_reactiva(monkeypatch, identidad_prueba, fallar):
    monkeypatch.syspath_prepend(str(ROOT / 'proyecto'))
    servicio = importlib.import_module('servicio')
    renders = []
    original = st.chat_input
    def entrada(*args, **kwargs):
        renders.append(kwargs.get('disabled', False))
        return original(*args, **kwargs)
    monkeypatch.setattr(st, 'chat_input', entrada)
    llamadas = []
    def responder(consulta):
        assert st.session_state.pensando is True
        assert renders[-1] is True
        llamadas.append(consulta)
        if fallar:
            raise RuntimeError('Fallo controlado de prueba')
        return {'respuesta':'Respuesta de prueba de UI', 'pasos':[]}
    monkeypatch.setattr(servicio, 'preguntar_orquestador', responder)
    app = AppTest.from_file(str(ROOT/'proyecto/ui/app.py'), default_timeout=20)
    app.session_state.auth_token = identidad_prueba
    app.run()
    assert not app.exception and not app.chat_input[0].disabled
    app.chat_input[0].set_value('Consulta de prueba').run()
    assert not app.exception
    assert llamadas == ['Consulta de prueba']
    assert True in renders
    assert not app.chat_input[0].disabled and not app.session_state['pensando']
    assert len(app.session_state['mensajes']) == 2


def test_iniciar_no_duplica_una_consulta_pendiente():
    source = ast.parse((ROOT/'proyecto/ui/app.py').read_text(encoding='utf-8'))
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name=='iniciar')
    state = SimpleNamespace(pensando=True, mensajes=[{'rol':'user','texto':'Original'}])
    rerun = Mock()
    namespace = {'st': SimpleNamespace(session_state=state, rerun=rerun)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<iniciar>', 'exec'), namespace)
    namespace['iniciar']('Duplicado')
    assert state.mensajes == [{'rol':'user','texto':'Original'}]
    rerun.assert_not_called()
