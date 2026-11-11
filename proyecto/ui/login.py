"""Componente reutilizable de acceso local.

La app arranca en modo demo y no monta este formulario. El equipo de frontend
puede importar ``render_login`` cuando integre la pantalla de inicio de sesión,
o abrir la app con ``?login=1`` para probarlo sin tocar el flujo principal.

Ahí solo se escriben usuario y clave de una identidad creada por el operador;
no se usa la contraseña de la base ni una API key.
"""

from __future__ import annotations

import streamlit as st

from proyecto.security.local_auth import authenticated, login, logout, principal


def identidad_actual(token: str | None):
    """Devuelve la identidad asociada al token, o ``None`` si ya no es válida."""
    if not token:
        return None
    try:
        with authenticated(token):
            return principal()
    except PermissionError:
        return None


def cerrar_sesion() -> None:
    """Cierra la sesión local y limpia el estado de Streamlit."""
    token = st.session_state.get("auth_token")
    if token:
        logout(token)
    st.session_state.clear()
    st.rerun()


def render_login(*, expanded: bool = True) -> None:
    """Renderiza el acceso local para integrarlo en una pantalla de login."""
    with st.expander("Acceso local", expanded=expanded):
        st.caption(
            "El acceso local identifica a la persona que usa la app. No es la "
            "clave de la base ni una API key: el operador crea el usuario y la "
            "clave, y aquí solo se escriben esas credenciales. Sirve para "
            "aplicar el alcance por cliente, los permisos y la auditoría."
        )
        token = st.session_state.get("auth_token")
        identidad = identidad_actual(token)
        if identidad:
            st.caption(
                f"Sesión local: {identidad['subject']} ({identidad['role']})"
            )
            if st.button("Cerrar sesión", key="logout_local"):
                cerrar_sesion()
            return

        st.caption("Pide al operador que aprovisione una identidad con:")
        st.code(
            "uv run python -m proyecto.security.local_auth --usuario "
            "usuario_prueba --rol normal --clientes 1",
            language="bash",
        )
        with st.form("login_local"):
            usuario = st.text_input("Usuario", key="login_usuario")
            clave = st.text_input("Clave", type="password", key="login_clave")
            entrar = st.form_submit_button("Entrar")
        if entrar:
            try:
                st.session_state.auth_token = login(usuario, clave)
                st.rerun()
            except PermissionError:
                st.error("Credenciales inválidas.")
