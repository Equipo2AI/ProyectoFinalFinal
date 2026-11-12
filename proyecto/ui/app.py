"""Interfaz Streamlit del agente.

    python -m streamlit run proyecto/ui/app.py
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
    procesar_documento,
    procesar_imagen_auto,
    reiniciar_orquestador,
)
from agents.document_intelligence_agent import (
    EXTENSIONES_DOCUMENTO,
    EXTENSIONES_IMAGEN,
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
)
from ui import componentes, estilos

TIPOS_ARCHIVO = sorted(
    extension.lstrip(".")
    for extension in EXTENSIONES_IMAGEN | EXTENSIONES_DOCUMENTO
)
MAX_ARCHIVO_BYTES = max(MAX_IMAGE_BYTES, MAX_DOCUMENT_BYTES)

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

st.session_state.setdefault('tema', 'oscuro')
estilos.aplicar(st.session_state.tema)
from ui.login import render_access
render_access()

for clave, valor in {
    "mensajes": [],
    "artefacto": None,
    "consulta_actual": None,
    "pensando": False,
    "consola_abierta": False,
    "tema": "oscuro",
    "archivo_actual": None,
    "archivo_nombre_actual": None,
    "archivo_tipo": None,
    "upload_dir": None,
}.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor

modo_demo = False
puede_consultar = True  # render_access ya valido la identidad


def _limpiar_archivo() -> None:
    directorio = st.session_state.get("upload_dir")
    if directorio:
        shutil.rmtree(directorio, ignore_errors=True)

    st.session_state.upload_dir = None


def _guardar_archivo_chat(archivo) -> tuple[str, str, str] | None:
    """Guarda una imagen o documento enviado desde el chat."""
    if archivo is None:
        return None

    sufijo = Path(archivo.name).suffix.lower()
    if sufijo in EXTENSIONES_IMAGEN:
        tipo = "imagen"
        limite = MAX_IMAGE_BYTES
        etiqueta_limite = "10 MB"
    elif sufijo in EXTENSIONES_DOCUMENTO:
        tipo = "documento"
        limite = MAX_DOCUMENT_BYTES
        etiqueta_limite = "25 MB"
    else:
        st.error(f"Formato no soportado: {sufijo or 'sin extensión'}")
        return None

    if archivo.size > limite:
        st.error(f"El archivo supera el límite de {etiqueta_limite}.")
        return None

    directorio = st.session_state.get("upload_dir")
    if not directorio:
        directorio = tempfile.mkdtemp(prefix="docint_ui_")
        st.session_state.upload_dir = directorio

    destino = Path(directorio) / f"{tipo}_{uuid.uuid4().hex}{sufijo}"
    destino.write_bytes(archivo.getvalue())
    return str(destino), archivo.name, tipo


def iniciar(
    texto: str,
    archivo_path: str | None = None,
    archivo_nombre: str | None = None,
    archivo_tipo: str | None = None,
) -> None:
    if st.session_state.pensando:
        return
    st.session_state.mensajes.append({"rol": "user", "texto": texto})
    st.session_state.consulta_actual = texto
    st.session_state.archivo_actual = archivo_path
    st.session_state.archivo_nombre_actual = archivo_nombre
    st.session_state.archivo_tipo = archivo_tipo
    st.session_state.pensando = True
    st.rerun()


def limpiar() -> None:
    _limpiar_archivo()
    reiniciar_orquestador()
    st.session_state.mensajes = []
    st.session_state.artefacto = None
    st.session_state.consulta_actual = None
    st.session_state.archivo_actual = None
    st.session_state.archivo_nombre_actual = None
    st.session_state.archivo_tipo = None
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

if modo_demo:
    st.caption(
        "Modo demo: identidad sintética con alcance completo. No se solicita "
        "login; los datos son de prueba."
    )
elif not puede_consultar:
    st.info(
        "La interfaz está visible, pero para consultar falta iniciar sesión "
        "con el componente de acceso local."
    )

if st.query_params.get("login") == "1":
    from ui.login import render_login

    with st.sidebar:
        render_login()

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
                        if st.button(
                            pregunta,
                            key=clave,
                            width="stretch",
                            disabled=not puede_consultar or st.session_state.pensando,
                        ):
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
                    if st.session_state.get("archivo_tipo") == "imagen":
                        estado = "Procesando imagen…"
                    elif st.session_state.get("archivo_tipo") == "documento":
                        estado = "Leyendo documento (un PDF escaneado puede tardar)…"
                    else:
                        estado = "Analizando la consulta…"
                    componentes.pensando(estado)

        entrada = st.chat_input(
            "Hazme una consulta o adjunta un archivo",
            accept_file=True,
            file_type=TIPOS_ARCHIVO,
            max_upload_size=MAX_ARCHIVO_BYTES // (1024 * 1024),
            disabled=not puede_consultar or st.session_state.pensando,
        )
        if entrada and not st.session_state.pensando:
            if isinstance(entrada, str):
                texto = entrada.strip()
                archivos = []
            else:
                texto = (entrada.text or "").strip()
                archivos = list(entrada.files or [])

            if archivos:
                guardado = _guardar_archivo_chat(archivos[0])
                if guardado:
                    ruta, nombre, tipo = guardado
                    predeterminado = (
                        "Analiza la imagen adjunta y responde lo más útil."
                        if tipo == "imagen"
                        else "Analiza el documento adjunto y responde lo más útil."
                    )
                    iniciar(
                        texto or predeterminado,
                        archivo_path=ruta,
                        archivo_nombre=nombre,
                        archivo_tipo=tipo,
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
        tipo_archivo = st.session_state.get("archivo_tipo")
        if tipo_archivo == "imagen":
            artefacto = procesar_imagen_auto(
                st.session_state.archivo_actual,
                st.session_state.consulta_actual or "",
                st.session_state.get("archivo_nombre_actual"),
            )
        elif tipo_archivo == "documento":
            artefacto = procesar_documento(
                st.session_state.archivo_actual,
                st.session_state.consulta_actual or "",
                st.session_state.get("archivo_nombre_actual"),
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
        st.session_state.archivo_actual = None
        st.session_state.archivo_nombre_actual = None
        st.session_state.archivo_tipo = None
    st.rerun()
