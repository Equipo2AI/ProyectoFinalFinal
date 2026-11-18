"""Guardrails deterministas sobre texto libre.

No sustituyen la autorización ni el guard SQL. Son una última barrera para
evitar que una respuesta del modelo publique PII evidente o cifras que no
aparezcan en la evidencia de las tools.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

PATRONES_PII = {
    "correo": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "telefono": re.compile(
        r"(?<!\d)(?:\+?52[\s-]?)?(?:\d{2,4}[\s-]){1,2}\d{4}(?!\d)"
    ),
    "telefono_contiguo": re.compile(r"(?<!\d)\d{10}(?!\d)"),
    "curp": re.compile(
        r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b",
        re.IGNORECASE,
    ),
    "rfc": re.compile(
        r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b",
        re.IGNORECASE,
    ),
}

_RE_NUMERO = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)(?![\w.])")
_RE_DATA_URI = re.compile(
    r"!\[[^\]]*\]\(\s*data:image/[^;\s]+;base64,[^)\s]+\s*\)",
    re.IGNORECASE,
)
_RE_BASE64_LARGO = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{500,}={0,2}")


def limpiar_base64(texto: str) -> str:
    """Evita que un PNG en base64 termine en el chat o en la respuesta final."""
    limpio = _RE_DATA_URI.sub("[gráfica generada]", str(texto or ""))
    return _RE_BASE64_LARGO.sub("[imagen omitida]", limpio)


def redactar_pii(texto: str) -> tuple[str, list[str]]:
    """Reemplaza PII evidente y devuelve los tipos encontrados."""
    resultado = str(texto or "")
    encontrados: list[str] = []
    for tipo, patron in PATRONES_PII.items():
        if patron.search(resultado):
            resultado = patron.sub("[DATO PROTEGIDO]", resultado)
            encontrados.append(tipo)
    return resultado, encontrados


def _numeros(texto: str) -> list[Decimal]:
    valores: list[Decimal] = []
    for coincidencia in _RE_NUMERO.finditer(str(texto or "")):
        crudo = coincidencia.group(1).replace(",", "")
        try:
            valores.append(Decimal(crudo))
        except InvalidOperation:
            continue
    return valores


def _es_ignorable(valor: Decimal) -> bool:
    # Años, meses, trimestres y números muy pequeños suelen ser contexto, no
    # cifras de negocio.
    if valor == valor.to_integral_value() and 1900 <= int(valor) <= 2100:
        return True
    return abs(valor) <= 12


def verificar_cifras(
    texto: str,
    evidencia: str | list[str] | dict | list[dict] | None,
    tolerancia: Decimal = Decimal("0.01"),
) -> list[str]:
    """Devuelve las cifras del texto que no aparecen en la evidencia.

    La comparación es numérica, no textual: ``1,000`` coincide con ``1000``.
    Los años y números pequeños se ignoran para no generar ruido.
    """
    if isinstance(evidencia, (dict, list)):
        fuente = json.dumps(evidencia, ensure_ascii=False, default=str)
    else:
        fuente = str(evidencia or "")
    disponibles = _numeros(fuente)
    no_verificadas: list[str] = []
    for valor in _numeros(texto):
        if _es_ignorable(valor):
            continue
        if any(abs(valor - candidato) <= tolerancia for candidato in disponibles):
            continue
        no_verificadas.append(str(valor))
    return list(dict.fromkeys(no_verificadas))


__all__: list[Any] = [
    "PATRONES_PII",
    "limpiar_base64",
    "redactar_pii",
    "verificar_cifras",
]
