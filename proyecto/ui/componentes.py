from __future__ import annotations

import html

import streamlit as st

_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round">{}</svg>'
)

_ICONOS = {
    "marca": _SVG.format(
        '<circle cx="12" cy="12" r="8.6"/>'
        '<circle cx="12" cy="12" r="2.4" fill="#C8102E" stroke="none"/>'
    ),
    "workflow": _SVG.format(
        '<rect width="8" height="8" x="3" y="3" rx="2"/>'
        '<path d="M7 11v4a2 2 0 0 0 2 2h4"/>'
        '<rect width="8" height="8" x="13" y="13" rx="2"/>'
    ),
    "db": _SVG.format(
        '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
        '<path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>'
    ),
    "chart": _SVG.format(
        '<path d="M3 3v18h18"/>'
        '<path d="M8 17V9"/><path d="M13 17V5"/><path d="M18 17v-5"/>'
    ),
    "book": _SVG.format(
        '<path d="M12 7v14"/>'
        '<path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5'
        'a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>'
    ),
    "shield": _SVG.format(
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
    "loader": _SVG.format('<path d="M21 12a9 9 0 1 1-6.219-8.56"/>'),
    "alerta": _SVG.format(
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "file": _SVG.format(
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
        '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
}

_AGENTES = {
    "orquestador": ("workflow", "Orquestador"),
    "data_steward": ("db", "Data Steward"),
    "data_analyst": ("chart", "Data Analyst"),
    "normatividad_rag": ("book", "Normativo"),
    "normativo": ("book", "Normativo"),
    "rag": ("book", "Normativo"),
    "compliance": ("shield", "Compliance"),
    "document_intelligence": ("file", "Documentos"),
}


def _esc(texto: object) -> str:
    return html.escape(str(texto))


def _agente(clave: object) -> tuple[str, str]:
    normalizada = str(clave).strip().lower().replace(" ", "_").replace("-", "_")
    return _AGENTES.get(normalizada, ("marca", str(clave)))


def texto(mensaje: str) -> str:
    return str(mensaje).replace("$", "\\$")


def cabecera(titulo: str, subtitulo: str) -> None:
    st.markdown(
        '<div class="topbar">'
        f'<div class="marca">{_ICONOS["marca"]}</div>'
        f'<div class="brand">{_esc(titulo)}<small>{_esc(subtitulo)}</small></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def hero() -> None:
    st.markdown(
        '<div class="hero">'
        '<div class="hero-kicker">Consulta</div>'
        '<div class="t">¿En qué te puedo ayudar?</div>'
        '<div class="s">Hola soy un agente de análisis de datos bancarios. Preguntame saldos, calidad de datos, normativa o definiciones del '
        'glosario.</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def pensando(estado: str = "Analizando la consulta…") -> None:
    st.markdown(
        '<div class="analizando">'
        f'<span class="analizando-ico">{_ICONOS["loader"]}</span>'
        f'<div><strong>{_esc(estado)}</strong>'
        '<small>El orquestador está eligiendo agentes</small></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def shimmer() -> None:
    st.markdown('<div class="shimmer"></div>', unsafe_allow_html=True)


def cabecera_consola(artefacto: dict | None, pensando: bool = False) -> None:
    estado = etiqueta = None
    if artefacto and artefacto.get("error"):
        estado, etiqueta = "error", "Error"
    elif pensando:
        estado, etiqueta = "pensando", "Analizando"
    elif artefacto:
        estado, etiqueta = "ok", "Completada"

    duracion = (artefacto or {}).get("duracion")
    tiempo = f" · {duracion:.1f} s" if duracion else ""
    estado_html = (
        f'<div class="estado {estado}" aria-live="polite">'
        f'<span class="dot"></span>{etiqueta}{tiempo}</div>'
        if estado
        else ""
    )

    st.markdown(
        '<div class="consola-head">'
        '<div class="rotulo">Traza y evidencia</div>'
        f"{estado_html}"
        "</div>",
        unsafe_allow_html=True,
    )


def traza(pasos: list[dict] | None) -> None:
    if not pasos:
        return

    filas = []
    for indice, paso in enumerate(pasos):
        icono, nombre = _agente(paso.get("agente", "orquestador"))
        estado = str(paso.get("estado") or "ok")
        accion = str(paso.get("accion") or "Intervino en la consulta")
        detalle = str(paso.get("detalle") or "")
        if detalle.strip().casefold() == accion.strip().casefold():
            detalle = ""

        duracion = paso.get("duracion")
        tiempo = f"{duracion:.1f} s" if duracion else ""
        ultimo = " ultimo" if indice == len(pasos) - 1 else ""

        filas.append(
            f'<div class="paso{ultimo}" data-estado="{_esc(estado)}">'
            f'<span class="nodo" title="{_esc(nombre)}">{_ICONOS[icono]}</span>'
            '<div class="paso-cuerpo">'
            f'<span class="paso-nombre">{_esc(nombre)}</span>'
            f'<span class="paso-accion">{_esc(accion)}</span>'
            + (f'<span class="paso-detalle">{_esc(detalle)}</span>' if detalle else "")
            + "</div>"
            + (f'<span class="paso-dur">{tiempo}</span>' if tiempo else "")
            + "</div>"
        )

    st.markdown(f'<div class="traza">{"".join(filas)}</div>', unsafe_allow_html=True)


def meta_mensaje(pasos: list[dict] | None, duracion: float | None = None) -> str:
    nombres: list[str] = []
    for paso in pasos or []:
        nombre = _agente(paso.get("agente", "orquestador"))[1]
        if nombre not in nombres:
            nombres.append(nombre)
    if not nombres:
        nombres = ["Orquestador"]

    tiempo = f'<span class="meta-tiempo">{duracion:.1f} s</span>' if duracion else ""
    return (
        '<div class="meta-mensaje">'
        f'<span class="meta-agentes">{" · ".join(_esc(n) for n in nombres)}</span>'
        f"{tiempo}</div>"
    )


def avisos(alertas: list[str]) -> None:
    for alerta in alertas:
        st.markdown(
            '<div class="aviso">'
            f'<span class="aviso-ico">{_ICONOS["alerta"]}</span>'
            "<div><strong>Alerta de calidad</strong>"
            f"<span>{_esc(alerta)}</span></div></div>",
            unsafe_allow_html=True,
        )


def error_consola(mensaje: str) -> None:
    st.markdown(
        '<div class="aviso error">'
        f'<span class="aviso-ico">{_ICONOS["alerta"]}</span>'
        "<div><strong>No se pudo completar</strong>"
        f"<span>{_esc(mensaje)}</span></div></div>",
        unsafe_allow_html=True,
    )


def seccion(titulo: str, cuenta: str | None = None) -> None:
    contador = f'<span class="cuenta">{_esc(cuenta)}</span>' if cuenta else ""
    st.markdown(
        f'<div class="seccion"><span class="seccion-t">{_esc(titulo)}</span>{contador}</div>',
        unsafe_allow_html=True,
    )


def fuentes(lista: list[str]) -> None:
    chips = "".join(
        f'<span class="fuente" title="{_esc(nombre)}">'
        f'{_ICONOS["file"]}<span>{_esc(nombre)}</span></span>'
        for nombre in lista
    )
    st.markdown(f'<div class="fuentes">{chips}</div>', unsafe_allow_html=True)


def _rotulo_columna(columna: object) -> str:
    texto = str(columna).replace("_", " ").strip()
    return texto[:1].upper() + texto[1:] if texto else texto


def _formato_celda(columna: object, valor: object) -> tuple[str, bool]:
    if valor is None:
        return "—", False
    if isinstance(valor, bool):
        return ("Sí" if valor else "No"), False
    if isinstance(valor, (int, float)):
        nombre = str(columna).casefold()
        if any(
            clave in nombre
            for clave in ("saldo", "monto", "importe", "promedio", "total", "ingreso", "egreso", "balance", "limite", "límite")
        ):
            return f"${valor:,.2f}", True
        if "pct" in nombre or "porcentaje" in nombre or "%" in nombre:
            return f"{valor:.1f}%", True
        if isinstance(valor, float):
            return f"{valor:,.2f}", True
        return f"{valor:,}", True
    return str(valor), False


def tabla_datos(datos: list[dict]) -> None:
    if not datos:
        return
    columnas = list(datos[0].keys())
    cabecera = "".join(
        f"<th>{_esc(_rotulo_columna(columna))}</th>" for columna in columnas
    )
    filas = []
    for fila in datos:
        celdas = []
        for columna in columnas:
            valor, numerico = _formato_celda(columna, fila.get(columna))
            clase = "num" if numerico else "txt"
            celdas.append(f'<td class="{clase}">{_esc(valor)}</td>')
        filas.append(f"<tr>{''.join(celdas)}</tr>")
    st.markdown(
        '<div class="tabla-datos-wrap"><table class="tabla-datos">'
        f"<thead><tr>{cabecera}</tr></thead>"
        f"<tbody>{''.join(filas)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def vacio_consola(pensando: bool = False) -> None:
    if pensando:
        st.markdown(
            '<div class="vacio-consola">'
            f'<span class="vacio-ico girando">{_ICONOS["loader"]}</span>'
            '<div class="t">Analizando la consulta…</div>'
            '<div class="s">El orquestador está decidiendo qué agentes '
            "intervenir.</div></div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="vacio-consola">'
        f'<span class="vacio-ico">{_ICONOS["workflow"]}</span>'
        '<div class="t">La traza aparecerá aquí</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def pie(texto_pie: str) -> None:
    st.markdown(f'<div class="pie-consola">{_esc(texto_pie)}</div>', unsafe_allow_html=True)
