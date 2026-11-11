"""Compatibilidad de opciones de carga con la version instalada de LangChain."""
from langchain_ollama import ChatOllama as _ChatOllama

from proyecto.config.models import opciones_ollama


class ChatOllama(_ChatOllama):
    def _chat_params(self, *args, **kwargs):
        params = super()._chat_params(*args, **kwargs)
        # use_mmap no es un campo del constructor en esta version. Debe viajar
        # dentro de options de la solicitud, conservando opciones explicitas.
        params['options'] = {**opciones_ollama(params['model']), **params['options']}
        return params
