"""Interfaz Streamlit del agente.

    uv run --with streamlit streamlit run proyecto/ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from servicio import preguntar, regraficar
from ui import componentes, estilos

SUGERENCIAS = [
    ("sug_0", "¿Cuál es el saldo promedio en febrero?"),
    ("sug_1", "¿Qué problemas de calidad tiene la tabla de saldos?"),
    ("sug_2", "¿Cómo se define un cliente activo en el glosario?"),
]

st.set_page_config(
    page_title="Asistente de Datos",
    page_icon=str(Path(__file__).parent / "icono.png"),
    layout="wide",
    initial_sidebar_state="collapsed",
)

for clave, valor in {
    "mensajes": [],
    "artefacto": None,
    "consulta_actual": None,
    "pensando": False,
    "consola_abierta": False,
    "tema": "oscuro",
}.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor

estilos.aplicar(st.session_state.tema)


def iniciar(texto: str) -> None:
    st.session_state.mensajes.append({"rol": "user", "texto": texto})
    st.session_state.consulta_actual = texto
    st.session_state.pensando = True
    st.session_state.consola_abierta = True
    st.rerun()


def limpiar() -> None:
    st.session_state.mensajes = []
    st.session_state.artefacto = None
    st.session_state.consulta_actual = None
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
        if st.session_state.artefacto:
            st.session_state.artefacto = regraficar(
                st.session_state.artefacto, st.session_state.tema
            )
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
            "Data Steward & Analyst",
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
                    componentes.pensando()

        consulta = st.chat_input("Pregunta sobre los datos…")
        if consulta:
            iniciar(consulta)

if abierta:
    with derecha:
        with st.container(key="consola"):
            with st.container(key="consola_head"):
                componentes.cabecera_consola(
                    st.session_state.artefacto, st.session_state.pensando
                )
            with st.container(key="consola_scroll"):
                panel_consola(st.session_state.artefacto, st.session_state.pensando)

if st.session_state.pensando:
    try:
        artefacto = preguntar(
            st.session_state.consulta_actual or "", st.session_state.tema
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
            }
        )
    finally:
        st.session_state.pensando = False
    st.rerun()
