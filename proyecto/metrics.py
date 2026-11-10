"""Catálogo declarativo de métricas del Data Analyst.

Las métricas no son SQL libre: son definiciones cerradas sobre vistas y
columnas existentes. El hub puede consultar el catálogo y pedir una métrica por
nombre; el backend construye la consulta con una allowlist.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Agregacion = Literal["count", "count_distinct", "sum", "avg"]


class Metrica(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nombre: str
    descripcion: str
    vista: str
    agregacion: Agregacion
    columna: str
    unidad: str
    dimensiones: list[str] = Field(default_factory=list)
    filtros: list[str] = Field(default_factory=list)
    alias: list[str] = Field(default_factory=list)

    def columnas_filtrables(self) -> set[str]:
        return set(self.dimensiones) | set(self.filtros)


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


METRICAS: tuple[Metrica, ...] = (
    Metrica(
        nombre="clientes",
        descripcion="Número de clientes distintos.",
        vista="vw_cliente_publico",
        agregacion="count_distinct",
        columna="num_cte",
        unidad="clientes",
        dimensiones=[
            "segmento",
            "genero",
            "tipo_persona",
            "giro",
            "territorio",
            "sucursal",
            "cve_segmento",
            "cve_promotor",
        ],
        alias=["numero_clientes", "total_clientes", "clientes_por_segmento"],
    ),
    Metrica(
        nombre="saldo_total",
        descripcion="Suma del saldo mensual reportado.",
        vista="vw_saldo_mensual",
        agregacion="sum",
        columna="saldo_mes",
        unidad="MXN",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
        alias=["saldo", "saldo_mensual", "saldo_por_producto"],
    ),
    Metrica(
        nombre="saldo_promedio",
        descripcion="Promedio del saldo mensual reportado.",
        vista="vw_saldo_mensual",
        agregacion="avg",
        columna="saldo_mes",
        unidad="MXN",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
    ),
    Metrica(
        nombre="transacciones",
        descripcion="Número de transacciones firmadas.",
        vista="vw_txn_firmada",
        agregacion="count",
        columna="num_cta",
        unidad="transacciones",
        dimensiones=[
            "cve_producto",
            "cve_txn",
            "cve_dominio",
            "cve_grupo",
            "fec_txn",
        ],
        alias=["numero_transacciones", "txn"],
    ),
    Metrica(
        nombre="importe_transacciones",
        descripcion="Suma del importe firmado de las transacciones.",
        vista="vw_txn_firmada",
        agregacion="sum",
        columna="importe_firmado",
        unidad="MXN",
        dimensiones=[
            "cve_producto",
            "cve_txn",
            "cve_dominio",
            "cve_grupo",
            "fec_txn",
        ],
        alias=["importe", "monto_transacciones"],
    ),
)

_POR_NOMBRE = {
    _normalizar(alias): metrica
    for metrica in METRICAS
    for alias in [metrica.nombre, *metrica.alias]
}


def obtener_metrica(nombre: str) -> Metrica | None:
    return _POR_NOMBRE.get(_normalizar(str(nombre or "").strip()))


def catalogo_metricas() -> list[dict]:
    return [
        metrica.model_dump()
        for metrica in METRICAS
    ]


__all__ = [
    "METRICAS",
    "Metrica",
    "catalogo_metricas",
    "obtener_metrica",
]
