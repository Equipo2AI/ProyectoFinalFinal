"""Serializacion de la mitigacion; no ejecuta inferencia ni llama Ollama."""
import os
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from proyecto.config.ollama_runtime import ChatOllama


class RuntimeOptionsTests(unittest.TestCase):
    def test_mmap_reaches_request_options_without_changing_model(self):
        with patch('sys.platform', 'win32'), patch.dict(os.environ, {'PROYECTO_OLLAMA_MMAP': '1'}):
            model = ChatOllama(model='mimo-v2.6', temperature=0)
            request = model._chat_params([HumanMessage(content='diagnostico')])
            self.assertTrue(request['options']['use_mmap'])
            self.assertEqual(request['options']['temperature'], 0)
            self.assertEqual(request['model'], 'mimo-v2.6')

    def test_opt_out_and_other_models_keep_original_options(self):
        with patch('sys.platform', 'win32'), patch.dict(os.environ, {'PROYECTO_OLLAMA_MMAP': '0'}):
            request = ChatOllama(model='mimo-v2.6')._chat_params([HumanMessage(content='hola')])
            self.assertNotIn('use_mmap', request['options'])
        request = ChatOllama(model='llama3.1:latest')._chat_params([HumanMessage(content='hola')])
        self.assertNotIn('use_mmap', request['options'])


if __name__ == '__main__':
    unittest.main()
