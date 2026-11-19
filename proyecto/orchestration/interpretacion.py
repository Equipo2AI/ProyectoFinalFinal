"""Interpretación determinista de la consulta y contrato de aclaración.

Este módulo no decide el plan de especialistas. Solo responde una pregunta
previa: ¿la consulta tiene suficiente información para intentar responder, o
falta un dato que cambiaría materialmente el resultado?

La interpretación es deliberadamente conservadora. No inventa periodos ni
filtros: si una expresión es ambigua, devuelve una pregunta con opciones. Si
el supuesto es seguro, lo declara para que el usuario pueda corregirlo.
"""

from __future__ import annotations

import re
import unicodedata


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


# "Últimos meses" no define una ventana. "Últimos 6 meses" sí.
_RE_PERIODO_RELATIVO = re.compile(
    r"\b(?:ultimos|ultimas)\s+(?:meses|semanas|dias|anos)\b"
    r"|\b(?:recientemente|ultimamente|hace\s+poco|en\s+los\s+ultimos\s+tiempos)\b",
    re.IGNORECASE,
)
_RE_PERIODO_EXPLICITO = re.compile(
    r"\b(?:ultimos?|ultimas?)\s+"
    r"(?:\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
    r"\s+(?:meses?|semanas?|dias?|anos?)\b"
    r"|\b(?:este|el|ultimo|ultima)\s+(?:mes|ano|trimestre|semana)\b"
    r"|\b(?:mes|semana|ano|trimestre)\s+(?:pasado|anterior)\b"
    r"|\bpor\s+(?:mes|meses|ano|anos|semana|semanas|dia|dias)\b"
    r"|\b(?:mensual|mensuales|anual|anuales|semanal|semanales|"
    r"diario|diarios)\b"
    r"|\b(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\b"
    r"|\b20\d{2}\b",
    re.IGNORECASE,
)

_RE_GRAFICA = re.compile(
    r"\b(?:grafica|grafico|graficar|visualiza\w*|chart)\b", re.IGNORECASE
)
_RE_DOCUMENTO_ADJUNTO = re.compile(
    r"\b(?:factura|recibo|comprobante|imagen|ocr|adjunto|archivo)\b",
    re.IGNORECASE,
)
# "El archivo de clientes" es un dataset; "la factura adjunta" es un archivo.
_RE_ARCHIVO_EXPLICITO = re.compile(
    r"\b(?:adjunto|factura|recibo|comprobante|imagen|ocr|pdf|docx|xlsx|"
    r"pptx|csv|ruta)\b",
    re.IGNORECASE,
)
_RE_RUTA_ARCHIVO = re.compile(
    r"(?:/|~)[^\s]+\.(?:png|jpg|jpeg|webp|gif|bmp|pdf|docx|xlsx|pptx|csv|html|txt|md)\b",
    re.IGNORECASE,
)
_RE_SALUDO = re.compile(
    r"^\s*(?:hola|buen[oa]s|hey|gracias|quien\s+eres|que\s+puedes\s+hacer)\b",
    re.IGNORECASE,
)
_RE_INFORMATIVA = re.compile(
    r"\b(?:que\s+es|quien\s+es|explica|como\s+funciona|para\s+que\s+sirve|"
    r"definicion)\b",
    re.IGNORECASE,
)
_RE_SALDOS_PLURAL = re.compile(
    r"\b(?:mis\s+saldos|dame\s+mis\s+saldos|saldos\s+de\s+mis\s+cuentas)\b",
    re.IGNORECASE,
)
_RE_SALDO_PERSONAL = re.compile(
    r"\b(?:mi\s+saldo|mi\s+cuenta|mis\s+cuentas)\b",
    re.IGNORECASE,
)
_RE_MOVIMIENTOS = re.compile(
    r"\b(?:movimientos?|gastos?|compras?|retiros?)\b", re.IGNORECASE
)
_RE_INYECCION = re.compile(
    r"\b(?:ignora|ignore|olvida|desobedece)\b.{0,80}"
    r"\b(?:instrucciones|reglas|indicaciones|restricciones|prompt)\b"
    r"|\b(?:jailbreak|system\s+prompt|prompt\s+del\s+sistema)\b",
    re.IGNORECASE | re.DOTALL,
)
_RE_DOMINIO = re.compile(
    r"\b(?:raw_|dim_|fact_|vw_)\w*\b|"
    r"\b(?:calidad|nulos?|duplicad\w*|faltantes?|esquema|tablas?|columnas?|"
    r"normativ\w*|politicas?|ley(?:es)?|manual(?:es)?|saldos?|clientes?|"
    r"cuentas?|movimientos?|gastos?|transacciones?|facturas?|recibos?|"
    r"documentos?|imagen(?:es)?|ocr|pdf|docx|xlsx|pptx|csv)\b",
    re.IGNORECASE,
)
_RE_RESPUESTA_CORTA = re.compile(
    r"\b(?:\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
    r"\s*(?:mes|meses|semana|semanas|dia|dias|ano|anos)\b"
    r"|\b(?:este\s+mes|este\s+ano|ultimo\s+mes|rango|otro\s+rango|"
    r"mes\s+pasado|semana\s+pasada|ano\s+pasado|trimestre\s+pasado|"
    r"mes\s+anterior|semana\s+anterior|ano\s+anterior|trimestre\s+anterior|"
    r"cuenta\s+personal|tarjeta|cheques|ahorro|credito\s+personal|"
    r"saldo\s+actual|promedio|evolucion|factura|recibo|documento|imagen|"
    r"archivo|ruta|adjunto|si|no|correcto|ambos|todas)\b",
    re.IGNORECASE,
)


def _tiene_contexto(contexto: str) -> bool:
    """Hay un resultado anterior que puede resolver una gráfica de seguimiento."""
    texto = _normalizar(contexto or "")
    return bool(
        texto
        and re.search(
            r"\b(?:saldo|promedio|movimiento|gasto|total|cliente|cuenta|"
            r"producto|monto|importe)\b",
            texto,
        )
    )


def _archivo_como_datos(texto: str) -> bool:
    """True para "el archivo de clientes", False para "la factura adjunta"."""
    return bool(
        re.search(r"\barchivos?\b", texto)
        and _RE_DOMINIO.search(texto)
        and not _RE_ARCHIVO_EXPLICITO.search(texto)
        and not _RE_RUTA_ARCHIVO.search(texto)
    )


def _slots(consulta: str) -> dict:
    texto = _normalizar(consulta)
    slots: dict = {}
    if _RE_GRAFICA.search(texto):
        slots["salida"] = "grafica"
    if re.search(r"\bsaldos?\b", texto):
        slots["metrica"] = "saldo"
    elif re.search(r"\bmovimientos?\b", texto):
        slots["metrica"] = "movimientos"
    elif re.search(r"\b(?:gastos?|compras?|retiros?)\b", texto):
        slots["metrica"] = "gastos"
    elif re.search(r"\bingresos?\b", texto):
        slots["metrica"] = "ingresos"
    elif re.search(r"\bpromedio\b", texto):
        slots["metrica"] = "promedio"
    elif re.search(r"\b(?:total|suma|conteo|cuantos?|cuantas?)\b", texto):
        slots["metrica"] = "conteo"
    elif re.search(r"\bclientes?\b", texto):
        slots["metrica"] = "clientes"
    elif re.search(r"\btransacciones?\b", texto):
        slots["metrica"] = "transacciones"

    entidad = re.search(
        r"\b(cuenta\s+personal|tarjeta(?:\s+de\s+credito)?|cheques|ahorro|"
        r"credito\s+personal|mi\s+cuenta|mis\s+cuentas)\b",
        texto,
    )
    if entidad:
        slots["entidad"] = entidad.group(1)

    if _RE_PERIODO_EXPLICITO.search(texto):
        slots["periodo"] = "explicito"
    elif _RE_PERIODO_RELATIVO.search(texto):
        slots["periodo"] = None
    return slots


def _resultado(
    consulta: str,
    *,
    slots: dict,
    lectura: str,
    supuestos: list[str] | None = None,
    pregunta: str = "",
    opciones: list[str] | None = None,
) -> dict:
    supuestos = [str(s).strip() for s in (supuestos or []) if str(s).strip()]
    opciones = [str(o).strip() for o in (opciones or []) if str(o).strip()]
    necesaria = bool(pregunta)
    if necesaria:
        suficiencia = "insuficiente"
    elif supuestos:
        suficiencia = "asumible"
    else:
        suficiencia = "suficiente"
    return {
        "consulta": consulta,
        "lectura": lectura,
        "slots": slots,
        "supuestos": supuestos,
        "suficiencia": suficiencia,
        "aclaracion": {
            "necesaria": necesaria,
            "pregunta": pregunta,
            "opciones": opciones,
        },
    }


def interpretar_consulta(
    consulta: str,
    *,
    contexto_analista: str = "",
    contexto_documentos: str = "",
    ultimo_agente: str | None = None,
    imagen_pendiente: str | None = None,
) -> dict:
    """Devuelve la lectura de la consulta y, si hace falta, una aclaración."""
    consulta = (consulta or "").strip()
    texto = _normalizar(consulta)
    slots = _slots(consulta)
    metrica = slots.get("metrica")
    entidad = slots.get("entidad")

    # Un intento de manipular las instrucciones no se convierte en una
    # pregunta de periodo: debe llegar al guard de seguridad del especialista.
    if _RE_INYECCION.search(texto):
        return _resultado(
            consulta,
            slots=slots,
            lectura="Intento de manipular las instrucciones del sistema",
        )

    # Un documento sin archivo ni contexto no se puede procesar todavía.
    if (
        _RE_DOCUMENTO_ADJUNTO.search(texto)
        and not _archivo_como_datos(texto)
        and not imagen_pendiente
        and not _RE_RUTA_ARCHIVO.search(consulta)
        and not contexto_documentos
    ):
        return _resultado(
            consulta,
            slots=slots,
            lectura="Analizar un documento o imagen",
            pregunta=(
                "Entiendo que quieres analizar un documento, pero no veo el "
                "archivo. ¿Puedes adjuntarlo o indicarme su ruta local?"
            ),
            opciones=["Adjuntar archivo", "Indicar ruta"],
        )

    # "Últimos meses" no es una ventana. No la convertimos en 6 ni en 3.
    if _RE_PERIODO_RELATIVO.search(texto):
        partes = [p for p in (metrica, entidad) if p]
        lectura = (
            "información sobre " + ", ".join(partes)
            if partes
            else "un periodo reciente"
        )
        return _resultado(
            consulta,
            slots=slots,
            lectura=lectura,
            pregunta=(
                f"Entiendo que buscas {lectura}, pero «últimos meses» "
                "no define una ventana. ¿Qué periodo quieres consultar?"
            ),
            opciones=["3 meses", "6 meses", "12 meses", "otro rango"],
        )

    # Una gráfica sin métrica ni contexto no se puede construir.
    if _RE_GRAFICA.search(texto) and not metrica:
        if _tiene_contexto(contexto_analista):
            supuestos = ["Usaré el resultado anterior como base para la gráfica."]
            return _resultado(
                consulta,
                slots=slots,
                lectura="Gráfica de seguimiento sobre el resultado anterior",
                supuestos=supuestos,
            )
        return _resultado(
            consulta,
            slots=slots,
            lectura="Solicitud de gráfica sin métrica",
            pregunta=(
                "Entiendo que quieres una gráfica, pero no sé qué métrica "
                "representar. ¿Qué quieres graficar?"
            ),
            opciones=[
                "saldo actual",
                "promedio mensual",
                "evolución por mes",
                "otra métrica",
            ],
        )

    # "Mis saldos" en plural puede ser saldo actual, promedio o evolución.
    if (
        _RE_SALDOS_PLURAL.search(texto)
        and not _RE_PERIODO_EXPLICITO.search(texto)
        and not _RE_GRAFICA.search(texto)
    ):
        return _resultado(
            consulta,
            slots=slots,
            lectura="Consulta de saldos personales",
            pregunta=(
                "Entiendo que quieres tus saldos. ¿Quieres el saldo actual, "
                "el promedio mensual o la evolución por mes?"
            ),
            opciones=["saldo actual", "promedio mensual", "evolución por mes"],
        )

    # Movimientos o gastos sin periodo pueden ser un rango enorme.
    if (
        _RE_MOVIMIENTOS.search(texto)
        and not _RE_PERIODO_EXPLICITO.search(texto)
        and not _tiene_contexto(contexto_analista)
    ):
        return _resultado(
            consulta,
            slots=slots,
            lectura="Consulta de movimientos o gastos sin periodo",
            pregunta=(
                "Entiendo que quieres revisar movimientos o gastos. "
                "¿Qué periodo quieres consultar?"
            ),
            opciones=["últimos 30 días", "este mes", "últimos 6 meses", "otro rango"],
        )

    # Consultas demasiado vagas: mejor pedir un dato concreto que adivinar.
    palabras = re.findall(r"[a-z0-9_]+", texto)
    if (
        len(palabras) <= 5
        and not metrica
        and not entidad
        and not _RE_DOMINIO.search(texto)
        and not _RE_SALUDO.search(texto)
        and not _RE_INFORMATIVA.search(texto)
        and not imagen_pendiente
        and not _RE_DOCUMENTO_ADJUNTO.search(texto)
        and not _RE_RUTA_ARCHIVO.search(consulta)
    ):
        return _resultado(
            consulta,
            slots=slots,
            lectura="Consulta sin un dato concreto",
            pregunta=(
                "No tengo suficiente contexto para responder. "
                "¿Qué dato, periodo o documento quieres consultar?"
            ),
            opciones=["un saldo", "un movimiento", "una gráfica", "un documento"],
        )

    supuestos: list[str] = []
    if (
        _RE_SALDO_PERSONAL.search(texto)
        and not _RE_PERIODO_EXPLICITO.search(texto)
        and not re.search(r"\b(?:promedio|actual|evolucion)\b", texto)
    ):
        supuestos.append("Mostraré el saldo más reciente disponible.")
    elif metrica == "movimientos" and contexto_analista:
        supuestos.append("Usaré el periodo o resultado de la conversación anterior.")

    lectura = consulta or "Consulta"
    if metrica or entidad:
        partes = [p for p in (metrica, entidad) if p]
        lectura = "Consulta sobre " + ", ".join(partes)
    return _resultado(
        consulta,
        slots=slots,
        lectura=lectura,
        supuestos=supuestos,
    )


def formatear_aclaracion(aclaracion: dict) -> str:
    """Convierte el contrato de aclaración en un mensaje para el usuario."""
    pregunta = str((aclaracion or {}).get("pregunta") or "").strip()
    opciones = [
        str(opcion).strip()
        for opcion in (aclaracion or {}).get("opciones") or []
        if str(opcion).strip()
    ]
    if not pregunta:
        pregunta = "Necesito una aclaración para continuar."
    if opciones:
        return f"{pregunta}\n\nOpciones: " + " · ".join(opciones)
    return pregunta


def es_respuesta_de_aclaracion(consulta: str) -> bool:
    """Heurística para saber si el turno actual responde a una pregunta previa."""
    texto = _normalizar(consulta or "")
    palabras = re.findall(r"[a-z0-9_]+", texto)
    if not palabras or len(palabras) > 8:
        return False
    # Una pregunta o una orden nueva no es una respuesta a la aclaración,
    # aunque mencione una palabra de las opciones.
    if "?" in consulta or re.search(
        r"^(?:que|como|por que|cual|cuando|donde)\b", texto
    ):
        return False
    if len(palabras) > 2 and re.search(
        r"\b(?:analiza|dame|muestra|revisa|explica|consulta|calcula|genera|haz)\b",
        texto,
    ):
        return False
    return bool(_RE_RESPUESTA_CORTA.search(texto))


def _frase_periodo(respuesta: str) -> str:
    """Da artículo a una respuesta de periodo («mes pasado» -> «el mes pasado»)."""
    normalizada = _normalizar(respuesta)
    if re.match(r"^(?:el|la|este|esta)\b", normalizada):
        return respuesta
    if re.match(r"^(?:ultimo|ultima)\b", normalizada):
        articulo = "la" if "semana" in normalizada else "el"
        return f"{articulo} {respuesta}"
    if "semana" in normalizada:
        return f"la {respuesta}"
    return f"el {respuesta}"


def completar_consulta(base: str, respuesta: str) -> str:
    """Combina la consulta pendiente con la respuesta de aclaración.

    No inventa un valor: solo reemplaza la expresión ambigua por lo que dijo
    el usuario. Si no reconoce el patrón, agrega la aclaración al final para
    que el planificador y el especialista tengan todo el contexto.
    """
    base = (base or "").strip()
    respuesta = (respuesta or "").strip()
    if not base:
        return respuesta
    if not respuesta:
        return base

    for unidad in ("meses", "mes", "semanas", "semana", "dias", "dia", "anos", "ano"):
        patron = re.compile(
            rf"\b(?:en\s+)?(?:los\s+)?[uú]ltimos?\s+{unidad}\b",
            re.IGNORECASE,
        )
        if patron.search(base):
            if re.search(
                r"\b(?:\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|"
                r"nueve|diez|once|doce)\b",
                _normalizar(respuesta),
            ):
                reemplazo = f"en los últimos {respuesta}"
            else:
                reemplazo = f"en {_frase_periodo(respuesta)}"
            return patron.sub(reemplazo, base, count=1)

    texto = _normalizar(base)
    if re.search(r"\b(?:periodo|rango|mes|meses|semana|dia|ano)\b", texto):
        return f"{base} (periodo: {respuesta})"
    if re.search(r"\b(?:grafica|grafico)\b", texto):
        return f"{base} de {respuesta}"

    if re.search(r"\bmis\s+saldos\b", texto):
        respuesta_norm = _normalizar(respuesta)
        if re.search(r"\bactual\b", respuesta_norm):
            return re.sub(
                r"\bmis\s+saldos\b",
                "mi saldo actual",
                base,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"\bpromedio\b", respuesta_norm):
            return re.sub(
                r"\bmis\s+saldos\b",
                "mi saldo promedio",
                base,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"\bevolucion\b", respuesta_norm):
            return re.sub(
                r"\bmis\s+saldos\b",
                "evolución de mi saldo por mes",
                base,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"\b\d+\s*(?:mes|meses|semana|semanas|dia|dias|ano|anos)\b", respuesta_norm):
            return re.sub(
                r"\bmis\s+saldos\b",
                f"mi saldo de los últimos {respuesta}",
                base,
                count=1,
                flags=re.IGNORECASE,
            )
        return f"{base} ({respuesta})"
    return f"{base}. Aclaración: {respuesta}"
