"""Resolución determinista de periodos en español.

La tool no consulta la base: convierte una expresión cerrada a un rango de
fechas y a las claves de ``dim_fecha`` (YYYYMMDD y YYYYMM). Si la expresión es
ambigua o no está soportada, falla en vez de adivinar.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
ABREVIATURAS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


class Periodo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expresion: str
    inicio: str
    fin: str
    tipo: str
    clave_inicio: int
    clave_fin: int
    anio_mes_inicio: int
    anio_mes_fin: int


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    limpio = "".join(c for c in normalizado if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", limpio).split())


def _fecha(valor: str | date | None) -> date:
    if valor is None:
        return date.today()
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor))


def _periodo(
    expresion: str,
    inicio: date,
    fin: date,
    tipo: str,
) -> Periodo:
    if fin < inicio:
        raise ValueError("El periodo termina antes de iniciar.")
    return Periodo(
        expresion=expresion,
        inicio=inicio.isoformat(),
        fin=fin.isoformat(),
        tipo=tipo,
        clave_inicio=int(inicio.strftime("%Y%m%d")),
        clave_fin=int(fin.strftime("%Y%m%d")),
        anio_mes_inicio=int(inicio.strftime("%Y%m")),
        anio_mes_fin=int(fin.strftime("%Y%m")),
    )


def _mes(expresion: str, anio: int, mes: int) -> Periodo:
    ultimo = calendar.monthrange(anio, mes)[1]
    return _periodo(expresion, date(anio, mes, 1), date(anio, mes, ultimo), "mes")


def _anio(expresion: str, anio: int) -> Periodo:
    return _periodo(expresion, date(anio, 1, 1), date(anio, 12, 31), "anio")


def _trimestre(expresion: str, anio: int, trimestre: int) -> Periodo:
    if not 1 <= trimestre <= 4:
        raise ValueError("El trimestre debe estar entre 1 y 4.")
    mes_inicio = (trimestre - 1) * 3 + 1
    mes_fin = mes_inicio + 2
    ultimo = calendar.monthrange(anio, mes_fin)[1]
    return _periodo(
        expresion,
        date(anio, mes_inicio, 1),
        date(anio, mes_fin, ultimo),
        "trimestre",
    )


def _mes_por_nombre(texto: str) -> tuple[int, int] | None:
    palabras = texto.split()
    for indice, palabra in enumerate(palabras):
        mes = MESES.get(palabra) or ABREVIATURAS.get(palabra)
        if not mes:
            continue
        anio = None
        if indice + 1 < len(palabras) and re.fullmatch(r"\d{4}", palabras[indice + 1]):
            anio = int(palabras[indice + 1])
        elif indice > 0 and re.fullmatch(r"\d{4}", palabras[indice - 1]):
            anio = int(palabras[indice - 1])
        if anio:
            return anio, mes
    return None


def resolver_periodo(
    expresion: str, hoy: str | date | None = None
) -> Periodo:
    """Convierte una expresión soportada a un rango de fechas.

    Ejemplos: ``2026``, ``2026-02``, ``2026-02-15``, ``febrero 2026``,
    ``1T 2026``, ``2026-Q2``, ``este mes``, ``mes pasado``, ``últimos 30 días``.
    """
    original = str(expresion or "").strip()
    texto = _normalizar(original)
    if not texto:
        raise ValueError("La expresión de periodo está vacía.")

    hoy_fecha = _fecha(hoy)

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", original):
        dia = date.fromisoformat(original)
        return _periodo(original, dia, dia, "dia")

    coincide = re.fullmatch(r"(\d{4})-(\d{2})", original)
    if coincide:
        anio, mes = int(coincide.group(1)), int(coincide.group(2))
        if not 1 <= mes <= 12:
            raise ValueError("El mes debe estar entre 01 y 12.")
        return _mes(original, anio, mes)

    coincide = re.fullmatch(r"(\d{4})", original)
    if coincide:
        return _anio(original, int(coincide.group(1)))

    coincide = re.fullmatch(r"(\d{4})\s*[- ]?\s*q([1-4])", texto)
    if coincide:
        return _trimestre(original, int(coincide.group(1)), int(coincide.group(2)))

    coincide = re.fullmatch(r"q([1-4])\s*(\d{4})", texto)
    if coincide:
        return _trimestre(original, int(coincide.group(2)), int(coincide.group(1)))

    coincide = re.fullmatch(r"([1-4])\s*t\s*(\d{4})", texto)
    if coincide:
        return _trimestre(original, int(coincide.group(2)), int(coincide.group(1)))

    coincide = re.fullmatch(r"([1-4])\s*trimestre\s*(\d{4})", texto)
    if coincide:
        return _trimestre(original, int(coincide.group(2)), int(coincide.group(1)))

    nombre_mes = _mes_por_nombre(texto)
    if nombre_mes:
        return _mes(original, *nombre_mes)

    if texto in {"hoy", "dia de hoy"}:
        return _periodo(original, hoy_fecha, hoy_fecha, "dia")
    if texto == "ayer":
        ayer = hoy_fecha - timedelta(days=1)
        return _periodo(original, ayer, ayer, "dia")

    if texto in {"este mes", "mes actual", "mes en curso"}:
        return _mes(original, hoy_fecha.year, hoy_fecha.month)
    if texto in {"mes pasado", "mes anterior"}:
        anio = hoy_fecha.year
        mes = hoy_fecha.month - 1
        if mes == 0:
            anio, mes = anio - 1, 12
        return _mes(original, anio, mes)

    if texto in {"este ano", "ano actual", "ano en curso"}:
        return _anio(original, hoy_fecha.year)
    if texto in {"ano pasado", "ano anterior"}:
        return _anio(original, hoy_fecha.year - 1)

    if texto in {"este trimestre", "trimestre actual"}:
        trimestre = (hoy_fecha.month - 1) // 3 + 1
        return _trimestre(original, hoy_fecha.year, trimestre)
    if texto in {"trimestre pasado", "trimestre anterior", "ultimo trimestre"}:
        trimestre = (hoy_fecha.month - 1) // 3 + 1
        anio = hoy_fecha.year
        trimestre -= 1
        if trimestre == 0:
            anio, trimestre = anio - 1, 4
        return _trimestre(original, anio, trimestre)

    coincide = re.fullmatch(r"ultimos? (\d{1,3}) (dias?|meses?)", texto)
    if coincide:
        cantidad = int(coincide.group(1))
        if cantidad <= 0 or cantidad > 366:
            raise ValueError("La cantidad debe estar entre 1 y 366.")
        if coincide.group(2).startswith("dia"):
            inicio = hoy_fecha - timedelta(days=cantidad - 1)
            return _periodo(original, inicio, hoy_fecha, "rango")
        mes_inicio = hoy_fecha.month - (cantidad - 1)
        anio_inicio = hoy_fecha.year
        while mes_inicio <= 0:
            mes_inicio += 12
            anio_inicio -= 1
        inicio = date(anio_inicio, mes_inicio, 1)
        fin = date(hoy_fecha.year, hoy_fecha.month, calendar.monthrange(hoy_fecha.year, hoy_fecha.month)[1])
        return _periodo(original, inicio, fin, "rango")

    raise ValueError(
        "No pude interpretar el periodo. Usa un formato como 2026, 2026-02, "
        "2026-02-15, febrero 2026, 1T 2026, este mes, mes pasado o "
        "últimos 30 días."
    )


__all__: list[Any] = ["MESES", "Periodo", "resolver_periodo"]
