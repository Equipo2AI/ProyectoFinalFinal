"""Presentacion del acceso: usa exclusivamente la autenticacion existente."""
import streamlit as st

from proyecto.security.local_auth import authenticated, login, logout, principal


def _clear(message=None):
    theme = st.session_state.get('tema', 'oscuro')
    st.session_state.clear()
    st.session_state.tema = theme
    if message:
        st.session_state.auth_notice = message
    st.rerun()


def render_access():
    """Finaliza el render hasta autenticar y vuelve a validar en cada rerun."""
    if st.session_state.get('auth_token'):
        try:
            with authenticated(st.session_state.auth_token):
                identity = principal()
        except PermissionError:
            logout(st.session_state.auth_token)
            _clear('Tu sesión venció o dejó de estar autorizada. Inicia sesión nuevamente.')
        role, action = st.columns([1, .22], vertical_alignment='center')
        with role:
            st.caption(f"Sesión: {identity['subject']} · {identity['role']}")
        with action:
            if st.button('Cerrar sesión', key='logout_local', width='stretch'):
                logout(st.session_state.auth_token)
                _clear('Sesión cerrada. Tu conversación ya no está disponible en esta pantalla.')
        return

    st.markdown('''<style>
    .st-key-login_card { max-width: 480px; margin: 7vh auto 3vh;
      padding: 32px; border: 1px solid var(--borde); border-radius: 24px;
      background: var(--panel, var(--papel)); color: var(--tinta); }
    .st-key-login_card h1 { font-size: 2rem; letter-spacing: -.04em; }
    .st-key-login_card [data-testid="stForm"] { border: 0; padding: 0; }
    .st-key-login_card input { background: var(--papel); color: var(--tinta);
      border-color: var(--borde); }
    .st-key-login_card label { color: var(--tinta); }
    .st-key-login_card button[kind="primaryFormSubmit"] { width: 100%; }
    </style>''', unsafe_allow_html=True)
    with st.container(key='login_card'):
        st.caption('ASISTENTE DE DATOS · ACCESO LOCAL')
        st.title('Bienvenido')
        st.write('Inicia sesión para consultar los datos autorizados para tu identidad.')
        if st.button('Cambiar tema', key='login_tema', help='Alternar tema claro y oscuro'):
            st.session_state.tema = 'claro' if st.session_state.tema == 'oscuro' else 'oscuro'
            st.rerun()
        notice = st.session_state.pop('auth_notice', None)
        if notice:
            st.info(notice)
        with st.form('login_local'):
            username = st.text_input('Identificador de usuario', key='login_usuario', autocomplete='username')
            password = st.text_input('Contraseña', type='password', key='login_clave', autocomplete='current-password')
            submitted = st.form_submit_button('Iniciar sesión', key='login_submit', type='primary', width='stretch')
        if submitted:
            try:
                with st.spinner('Verificando credenciales…'):
                    token = login(username.strip(), password)
                st.session_state.auth_token = token
                st.session_state.pop('login_clave', None)
                st.rerun()
            except PermissionError:
                st.error('Usuario o contraseña incorrectos. Revisa tus credenciales e inténtalo de nuevo.')
        st.caption('Las cuentas y permisos los configura el operador. El chat no puede cambiar tu rol.')
    st.stop()


def identidad_actual(token):
    """Interfaz del equipo sobre la misma sesion autenticada."""
    if not token:
        return None
    try:
        with authenticated(token):
            return principal()
    except PermissionError:
        return None


def cerrar_sesion():
    token = st.session_state.get('auth_token')
    if token:
        logout(token)
    _clear('Sesion cerrada.')


def render_login(*, expanded=True):
    """Compatibilidad con el componente del equipo; exige autenticacion."""
    render_access()
