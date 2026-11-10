"""Interfaz Streamlit del agente.

    uv run --with streamlit streamlit run proyecto/ui/app.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from servicio import (
    preguntar_orquestador,
    procesar_imagen_auto,
    reiniciar_orquestador,
)
from agents.document_intelligence_agent import EXTENSIONES_IMAGEN, MAX_IMAGE_BYTES
from ui import componentes, estilos

TIPOS_IMAGEN = sorted(extension.lstrip(".") for extension in EXTENSIONES_IMAGEN)

SUGERENCIAS = [
    (
        "sug_0",
        "¿Qué medidas de seguridad exige la Ley Federal de Protección de "
        "Datos Personales en Posesión de los Particulares?",
    ),
    (
        "sug_1",
        "¿Cuántos clientes hay en la base y cómo se distribuyen por "
        "tipo de persona?",
    ),
    (
        "sug_2",
        "¿Qué dice el aviso de privacidad de Banamex sobre el tratamiento de "
        "datos personales?",
    ),
]

st.set_page_config(
    page_title="Asistente de Datos",
    page_icon=str(Path(__file__).parent / "icono.png"),
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Desarrollo local: solo el operador aprovisiona identidades y permisos.
from proyecto.security.local_auth import login, logout, authenticated, principal

if not st.session_state.get('auth_token'):
    st.title('Acceso local de desarrollo')
    with st.form('login_local'):
        usuario = st.text_input('Usuario', key='login_usuario')
        clave = st.text_input('Clave', type='password', key='login_clave')
        entrar = st.form_submit_button('Entrar')
    if entrar:
        try:
            st.session_state.auth_token = login(usuario, clave)
            st.rerun()
        except PermissionError:
            st.error('Credenciales invalidas.')
    st.stop()

try:
    with authenticated(st.session_state.auth_token):
        identidad = principal()
    st.caption(f"Sesion local: {identidad['subject']} ({identidad['role']})")
except PermissionError:
    st.session_state.clear()
    st.rerun()

if st.button('Cerrar sesion', key='logout_local'):
    logout(st.session_state.auth_token)
    st.session_state.clear()
    st.rerun()

for clave, valor in {
    "mensajes": [],
    "artefacto": None,
    "consulta_actual": None,
    "pensando": False,
    "consola_abierta": False,
    "tema": "oscuro",
    "imagen_actual": None,
    "imagen_nombre_actual": None,
    "upload_dir": None,
}.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor

estilos.aplicar(st.session_state.tema)


def _limpiar_imagen() -> None:
    directorio = st.session_state.get("upload_dir")
    if directorio:
        shutil.rmtree(directorio, ignore_errors=True)

    st.session_state.upload_dir = None


def _guardar_imagen_chat(archivo) -> tuple[str, str] | None:
    """Guarda la imagen enviada desde el chat en un directorio temporal."""
    if archivo is None:
        return None

    if archivo.size > MAX_IMAGE_BYTES:
        st.error("La imagen supera el límite de 10 MB.")
        return None

    sufijo = Path(archivo.name).suffix.lower()
    if sufijo not in EXTENSIONES_IMAGEN:
        st.error(f"Formato no soportado: {sufijo}")
        return None

    directorio = st.session_state.get("upload_dir")
    if not directorio:
        directorio = tempfile.mkdtemp(prefix="docint_ui_")
        st.session_state.upload_dir = directorio

    destino = Path(directorio) / f"imagen_{uuid.uuid4().hex}{sufijo}"
    destino.write_bytes(archivo.getvalue())
    return str(destino), archivo.name


def iniciar(
    texto: str,
    imagen_path: str | None = None,
    imagen_nombre: str | None = None,
) -> None:
    st.session_state.mensajes.append({"rol": "user", "texto": texto})
    st.session_state.consulta_actual = texto
    st.session_state.imagen_actual = imagen_path
    st.session_state.imagen_nombre_actual = imagen_nombre
    st.session_state.pensando = True
    st.rerun()


def limpiar() -> None:
    _limpiar_imagen()
    reiniciar_orquestador()
    st.session_state.mensajes = []
    st.session_state.artefacto = None
    st.session_state.consulta_actual = None
    st.session_state.imagen_actual = None
    st.session_state.imagen_nombre_actual = None
    st.session_state.pensando = False
    st.session_state.consola_abierta = False
    st.rerun()


def boton_panel(abierta: bool) -> None:
    etiqueta = "Ocultar traza" if abierta else "Mostrar traza"
    clave = "btn_cerrar" if abierta else "btn_abrir"
    if st.button(etiqueta, key=clave, help=etiqueta):
        st.session_state.consola_abierta = not abierta
        st.rerun()


def boton_tema() -> None:
    if st.button("Cambiar tema", key="btn_tema", help="Cambiar entre claro y oscuro"):
        st.session_state.tema = "claro" if st.session_state.tema == "oscuro" else "oscuro"
        st.rerun()


def _pasos(artefacto: dict) -> list[dict]:
    pasos = artefacto.get("pasos") or []
    if pasos:
        return pasos
    return [
        {"agente": agente, "accion": "Intervino en la consulta"}
        for agente in artefacto.get("agentes", [])
    ]


def panel_consola(artefacto: dict | None, pensando: bool) -> None:
    if artefacto is None:
        componentes.vacio_consola(pensando)
        return

    componentes.traza(_pasos(artefacto))

    error = artefacto.get("error")
    if error:
        componentes.error_consola(str(error))

    alertas = artefacto.get("alertas") or []
    if alertas:
        cuenta = "1 alerta" if len(alertas) == 1 else f"{len(alertas)} alertas"
        componentes.seccion("Alertas", cuenta)
        componentes.avisos(alertas)

    sql = artefacto.get("sql")
    datos = artefacto.get("datos") or []
    grafica = artefacto.get("grafica")
    fuentes = artefacto.get("fuentes") or []

    if grafica:
        componentes.seccion("Gráfica")
        st.image(grafica, width="stretch")
        st.download_button(
            "Descargar gráfica",
            data=grafica,
            file_name="grafica.png",
            mime="image/png",
            key="descargar_grafica",
            icon=":material/download:",
        )

    if datos:
        componentes.seccion("Datos", f"{len(datos)} filas")
        componentes.tabla_datos(datos)

    if sql:
        lineas = len(str(sql).strip().splitlines())
        componentes.seccion("Consulta SQL", f"{lineas} líneas")
        with st.expander("Ver consulta", expanded=False):
            st.code(sql, language="sql")

    if fuentes:
        cuenta = "1 fuente" if len(fuentes) == 1 else f"{len(fuentes)} fuentes"
        componentes.seccion("Fuentes", cuenta)
        componentes.fuentes(fuentes)

    modelo = artefacto.get("modelo")
    duracion = artefacto.get("duracion")
    nota = artefacto.get("nota")
    pie = " · ".join(
        parte
        for parte in (
            str(modelo) if modelo else "",
            f"{duracion:.1f} s" if duracion else "",
            str(nota) if nota else "",
        )
        if parte
    )
    if pie:
        componentes.pie(pie)


abierta = st.session_state.consola_abierta

with st.container(key="topbar"):
    columna_marca, columna_tema, columna_accion = st.columns(
        [1, 0.06, 0.06], vertical_alignment="center"
    )
    with columna_marca:
        componentes.cabecera(
            "Asistente de Datos",
            "Data Steward · Analytics · Documentos",
        )
    with columna_tema:
        boton_tema()
    with columna_accion:
        boton_panel(abierta)

if abierta:
    izquierda, derecha = st.columns([1.55, 1], gap="medium")
else:
    izquierda = st.container()

with izquierda:
    with st.container(key="chat"):
        if st.session_state.pensando:
            componentes.shimmer()
        with st.container(key="chat_head"):
            st.markdown('<span class="rotulo">Conversación</span>', unsafe_allow_html=True)
            if st.session_state.mensajes:
                if st.button("Limpiar", key="limpiar", help="Borrar la conversación y la traza"):
                    limpiar()

        with st.container(key="chat_scroll"):
            if not st.session_state.mensajes:
                componentes.hero()
                columnas = st.columns(3, gap="small")
                for (clave, pregunta), columna in zip(SUGERENCIAS, columnas):
                    with columna:
                        if st.button(pregunta, key=clave, width="stretch"):
                            iniciar(pregunta)
            else:
                for mensaje in st.session_state.mensajes:
                    with st.chat_message(mensaje["rol"]):
                        if mensaje.get("formato") == "texto":
                            st.code(mensaje["texto"], language="text")
                        else:
                            st.markdown(componentes.texto(mensaje["texto"]))
                        if mensaje["rol"] == "assistant":
                            st.markdown(
                                componentes.meta_mensaje(
                                    mensaje.get("pasos"),
                                    mensaje.get("duracion"),
                                ),
                                unsafe_allow_html=True,
                            )
                if st.session_state.pensando:
                    estado = (
                        "Procesando imagen…"
                        if st.session_state.get("imagen_actual")
                        else "Analizando la consulta…"
                    )
                    componentes.pensando(estado)

        entrada = st.chat_input(
            "Hazme una consulta",
            accept_file=True,
            file_type=TIPOS_IMAGEN,
            max_upload_size=MAX_IMAGE_BYTES // (1024 * 1024),
        )
        if entrada:
            if isinstance(entrada, str):
                texto = entrada.strip()
                archivos = []
            else:
                texto = (entrada.text or "").strip()
                archivos = list(entrada.files or [])

            if archivos:
                guardado = _guardar_imagen_chat(archivos[0])
                if guardado:
                    ruta, nombre = guardado
                    iniciar(
                        texto or "Analiza la imagen adjunta",
                        imagen_path=ruta,
                        imagen_nombre=nombre,
                    )
            elif texto:
                iniciar(texto)

if abierta:
    with derecha:
        with st.container(key="consola"):
            with st.container(key="consola_head"):
                componentes.cabecera_consola(
                    st.session_state.artefacto, st.session_state.pensando
                )
            with st.container(key="consola_scroll"):
                panel_consola(st.session_state.artefacto, st.session_state.pensando)

# Autoscroll fuera del contenedor del chat. Si el iframe vive dentro de
# chat_scroll altera su scrollHeight y puede colapsar el layout durante el
# rerun. El bucle reintenta mientras Streamlit termina de montar los mensajes.
if st.session_state.mensajes:
    firma = f"{len(st.session_state.mensajes)}-{len(st.session_state.mensajes[-1].get('texto', ''))}"
    st.iframe(
        f"""
        <script>
        const doc = window.parent.document;
        const inicio = performance.now();
        const alFinal = () => {{
          const contenedores = [...doc.querySelectorAll('.st-key-chat_scroll')];
          const el = contenedores.find((n) => n.offsetParent !== null && n.clientHeight > 0)
            || contenedores[contenedores.length - 1];
          if (el) el.scrollTop = el.scrollHeight;
        }};
        const bucle = () => {{
          alFinal();
          if (performance.now() - inicio < 2500) requestAnimationFrame(bucle);
        }};
        bucle();
        /* {firma} */
        </script>
        """,
        height=1,
    )

if st.session_state.pensando:
    try:
        if st.session_state.get("imagen_actual"):
            artefacto = procesar_imagen_auto(
                st.session_state.imagen_actual,
                st.session_state.consulta_actual or "",
                st.session_state.get("imagen_nombre_actual"),
            )
        else:
            artefacto = preguntar_orquestador(
                st.session_state.consulta_actual or ""
            )
    except Exception as exc:  # noqa: BLE001
        mensaje = (
            "No pude completar la consulta. Verifica que el orquestador y los "
            "modelos estén disponibles e inténtalo de nuevo."
        )
        pasos = [
            {
                "agente": "orquestador",
                "accion": "Falló la consulta",
                "detalle": type(exc).__name__,
                "estado": "error",
            }
        ]
        st.session_state.artefacto = {
            "respuesta": mensaje,
            "pasos": pasos,
            "error": mensaje,
        }
        st.session_state.mensajes.append(
            {"rol": "assistant", "texto": mensaje, "pasos": pasos, "duracion": None}
        )
    else:
        pasos = artefacto.get("pasos") or []
        st.session_state.artefacto = artefacto
        st.session_state.mensajes.append(
            {
                "rol": "assistant",
                "texto": artefacto.get("respuesta", ""),
                "pasos": pasos,
                "duracion": artefacto.get("duracion"),
                "formato": artefacto.get("formato"),
            }
        )
    finally:
        st.session_state.pensando = False
        st.session_state.imagen_actual = None
        st.session_state.imagen_nombre_actual = None
    st.rerun()
