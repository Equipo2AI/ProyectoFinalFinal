"""Interfaz Streamlit del agente.

    uv run --with streamlit streamlit run proyecto/ui/app.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from servicio import (
    EXTENSIONES_IMAGEN,
    MAX_IMAGE_BYTES,
    preguntar,
    preguntar_orquestador,
    procesar_imagen,
    regraficar,
    reiniciar_orquestador,
)
from ui import componentes, estilos

TIPOS_IMAGEN = sorted(extension.lstrip(".") for extension in EXTENSIONES_IMAGEN)

SUGERENCIAS_DEMO = [
    ("sug_0", "¿Cuál es el saldo promedio en febrero?"),
    ("sug_1", "¿Qué problemas de calidad tiene la tabla de saldos?"),
    ("sug_2", "¿Cómo se define un cliente activo en el glosario?"),
]

SUGERENCIAS_ORQUESTADOR = [
    ("sug_0", "¿Qué problemas de calidad tiene el CSV de clientes?"),
    ("sug_1", "¿Qué columnas y tipos tiene el CSV de clientes?"),
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
    "motor": "orquestador",
    "motor_actual": "orquestador",
    "imagen_path": None,
    "imagen_nombre": None,
    "imagen_actual": None,
    "operacion_actual": "ocr",
    "operacion_imagen": "ocr",
    "uploader_key": 0,
    "upload_dir": None,
    "imagen_firma": None,
    "mostrar_adjunto": False,
}.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor

estilos.aplicar(st.session_state.tema)


def _limpiar_imagen() -> None:
    ruta = st.session_state.get("imagen_path")
    if ruta:
        try:
            Path(ruta).unlink(missing_ok=True)
        except OSError:
            pass

    directorio = st.session_state.get("upload_dir")
    if directorio:
        shutil.rmtree(directorio, ignore_errors=True)

    st.session_state.imagen_path = None
    st.session_state.imagen_nombre = None
    st.session_state.upload_dir = None
    st.session_state.imagen_firma = None


def _guardar_imagen(archivo) -> None:
    """Guarda la imagen subida en un directorio temporal de la sesión."""
    if archivo is None:
        if st.session_state.get("imagen_path") and not st.session_state.get("pensando"):
            _limpiar_imagen()
        return

    if archivo.size > MAX_IMAGE_BYTES:
        st.error("La imagen supera el límite de 10 MB.")
        return

    sufijo = Path(archivo.name).suffix.lower()
    if sufijo not in EXTENSIONES_IMAGEN:
        st.error(f"Formato no soportado: {sufijo}")
        return

    firma = (archivo.name, archivo.size)
    if (
        st.session_state.get("imagen_firma") == firma
        and st.session_state.get("imagen_path")
    ):
        return

    directorio = st.session_state.get("upload_dir")
    if not directorio:
        directorio = tempfile.mkdtemp(prefix="docint_ui_")
        st.session_state.upload_dir = directorio

    anterior = st.session_state.get("imagen_path")
    if anterior:
        try:
            Path(anterior).unlink(missing_ok=True)
        except OSError:
            pass

    destino = Path(directorio) / f"imagen{sufijo}"
    destino.write_bytes(archivo.getvalue())
    st.session_state.imagen_path = str(destino)
    st.session_state.imagen_nombre = archivo.name
    st.session_state.imagen_firma = firma


def iniciar(
    texto: str,
    imagen_path: str | None = None,
    operacion: str = "ocr",
    imagen_nombre: str | None = None,
    motor: str | None = None,
) -> None:
    st.session_state.mensajes.append(
        {"rol": "user", "texto": texto, "imagen": imagen_nombre}
    )
    st.session_state.consulta_actual = texto
    st.session_state.imagen_actual = imagen_path
    st.session_state.operacion_actual = operacion
    st.session_state.motor_actual = motor or st.session_state.get("motor", "orquestador")
    if imagen_path is None:
        st.session_state.mostrar_adjunto = False
    st.session_state.pensando = True
    st.session_state.consola_abierta = True
    st.rerun()


def limpiar() -> None:
    _limpiar_imagen()
    reiniciar_orquestador()
    st.session_state.mensajes = []
    st.session_state.artefacto = None
    st.session_state.consulta_actual = None
    st.session_state.imagen_actual = None
    st.session_state.operacion_actual = "ocr"
    st.session_state.motor_actual = st.session_state.get("motor", "orquestador")
    st.session_state.mostrar_adjunto = False
    st.session_state.pensando = False
    st.session_state.consola_abierta = False
    st.session_state.uploader_key += 1
    st.session_state.operacion_imagen = "ocr"
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
                sugerencias = (
                    SUGERENCIAS_ORQUESTADOR
                    if st.session_state.get("motor") == "orquestador"
                    else SUGERENCIAS_DEMO
                )
                columnas = st.columns(3, gap="small")
                for (clave, pregunta), columna in zip(sugerencias, columnas):
                    with columna:
                        if st.button(pregunta, key=clave, width="stretch"):
                            iniciar(pregunta)
            else:
                for mensaje in st.session_state.mensajes:
                    with st.chat_message(mensaje["rol"]):
                        if mensaje.get("imagen"):
                            st.caption(f"Imagen adjunta: {mensaje['imagen']}")
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

        with st.container(key="selector_motor"):
            st.radio(
                "Motor de consulta de texto",
                options=["orquestador", "demo"],
                format_func=lambda valor: (
                    "Orquestador MCP" if valor == "orquestador" else "Demo"
                ),
                horizontal=True,
                key="motor",
                help=(
                    "El orquestador usa los servidores MCP. El modo demo usa "
                    "respuestas sintéticas. Las imágenes siempre usan MCP."
                ),
            )

        if st.session_state.get("mostrar_adjunto"):
            with st.container(key="adjunto_panel"):
                st.markdown(
                    '<div class="adjunto-titulo">Adjuntar imagen</div>',
                    unsafe_allow_html=True,
                )
                archivo = st.file_uploader(
                    "Imagen",
                    type=TIPOS_IMAGEN,
                    key=f"uploader_{st.session_state.uploader_key}",
                    help="PNG, JPG, WEBP, GIF o BMP. Máximo 10 MB.",
                    label_visibility="collapsed",
                )
                _guardar_imagen(archivo)

                if st.session_state.get("imagen_path"):
                    st.caption(f"Imagen lista: {st.session_state.imagen_nombre}")
                    st.image(st.session_state.imagen_path, width=180)
                    operacion = st.radio(
                        "Operación sobre la imagen",
                        options=["ocr", "descripcion"],
                        format_func=lambda valor: (
                            "Extraer texto (OCR)"
                            if valor == "ocr"
                            else "Describir imagen"
                        ),
                        horizontal=True,
                        key="operacion_imagen",
                    )
                    if st.button(
                        "Procesar imagen",
                        key="procesar_imagen",
                        width="stretch",
                    ):
                        iniciar(
                            "Procesa la imagen adjunta",
                            imagen_path=st.session_state.imagen_path,
                            operacion=operacion,
                            imagen_nombre=st.session_state.imagen_nombre,
                        )

        adjunto_abierto = st.session_state.get("mostrar_adjunto", False)
        etiqueta_adjunto = "Cerrar adjunto" if adjunto_abierto else "Adjuntar imagen"
        clave_adjunto = "cerrar_adjunto" if adjunto_abierto else "abrir_adjunto"
        if st.button(etiqueta_adjunto, key=clave_adjunto, help=etiqueta_adjunto):
            st.session_state.mostrar_adjunto = not adjunto_abierto
            st.rerun()

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
        if st.session_state.get("imagen_actual"):
            artefacto = procesar_imagen(
                st.session_state.imagen_actual,
                st.session_state.operacion_actual,
            )
        elif st.session_state.get("motor_actual") == "demo":
            artefacto = preguntar(
                st.session_state.consulta_actual or "", st.session_state.tema
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
    st.rerun()
