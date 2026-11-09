"""Conecta la pantalla con el backend sin transformar sus respuestas."""

from __future__ import annotations

from time import perf_counter

import streamlit as st


def _get_orquestador():
    # Una instancia por sesión; importar no ejecuta consultas a los modelos.
    if "ui_orquestador" not in st.session_state:
        from proyecto.orchestration.orquestador import Orquestador

        st.session_state.ui_orquestador = Orquestador()
    return st.session_state.ui_orquestador


def preguntar_orquestador(consulta: str) -> dict:
    orquestador = _get_orquestador()
    inicio = perf_counter()
    respuesta = orquestador.procesar(consulta)
    agentes = list(dict.fromkeys(orquestador.agentes_invocados))
    return {
        "respuesta": respuesta,
        "agentes": agentes,
        "pasos": [
            {"agente": nombre, "accion": "Invocación registrada por el orquestador"}
            for nombre in agentes
        ],
        "duracion": perf_counter() - inicio,
        "modelo": orquestador.modelo,
        "nota": "Se muestran agentes invocados; sin detalle de herramientas MCP.",
    }


def reiniciar_orquestador() -> None:
    orquestador = st.session_state.get("ui_orquestador")
    if orquestador is not None:
        orquestador.reiniciar()
