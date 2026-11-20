"""Catálogo declarativo de métricas del Data Analyst.

Las métricas no son SQL libre: son definiciones cerradas sobre vistas y
columnas existentes. El orquestador puede consultar el catálogo y pedir una métrica por
nombre; el backend construye la consulta con una allowlist.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Agregacion = Literal["count", "count_distinct", "sum", "avg"]
TipoMetrica = Literal["snapshot", "flujo", "conteo"]


class Metrica(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nombre: str
    descripcion: str
    vista: str
    agregacion: Agregacion
    columna: str
    unidad: str
    tipo: TipoMetrica = "flujo"
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
        tipo="conteo",
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
        alias=[
            "numero_clientes",
            "total_clientes",
            "clientes_por_segmento",
            "clientes_distintos",
        ],
    ),
    Metrica(
        nombre="saldo_total",
        descripcion="Suma del saldo de cierre en un solo corte.",
        vista="vw_saldo_mensual",
        agregacion="sum",
        columna="saldo_mes",
        unidad="MXN",
        tipo="snapshot",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
        filtros=["cve_fecha_informacion"],
        alias=[
            "saldo",
            "saldo_mensual",
            "saldo_por_producto",
            "saldo_cierre",
            "saldo_de_cierre",
        ],
    ),
    Metrica(
        nombre="saldo_apertura",
        descripcion="Suma del saldo de apertura en un solo corte.",
        vista="vw_saldo_mensual",
        agregacion="sum",
        columna="saldo_apertura",
        unidad="MXN",
        tipo="snapshot",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
        filtros=["cve_fecha_informacion"],
        alias=["saldo_inicial", "apertura"],
    ),
    Metrica(
        nombre="saldo_promedio",
        descripcion="Promedio del saldo de cierre en un solo corte.",
        vista="vw_saldo_mensual",
        agregacion="avg",
        columna="saldo_mes",
        unidad="MXN",
        tipo="snapshot",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
        filtros=["cve_fecha_informacion"],
        alias=["saldo_medio", "promedio_saldo"],
    ),
    Metrica(
        nombre="cuentas",
        descripcion="Número de cuentas distintas.",
        vista="vw_saldo_mensual",
        agregacion="count_distinct",
        columna="num_cta",
        unidad="cuentas",
        tipo="conteo",
        dimensiones=[
            "anio_mes",
            "cve_producto",
            "cve_dominio",
            "cve_grupo",
        ],
        filtros=["cve_fecha_informacion"],
        alias=[
            "numero_cuentas",
            "total_cuentas",
            "cuentas_distintas",
            "cuentas_por_producto",
        ],
    ),
    Metrica(
        nombre="transacciones",
        descripcion="Número de transacciones firmadas.",
        vista="vw_txn_firmada",
        agregacion="count",
        columna="num_cta",
        unidad="transacciones",
        tipo="conteo",
        dimensiones=[
            "cve_producto",
            "cve_txn",
            "cve_dominio",
            "cve_grupo",
            "fec_txn",
        ],
        filtros=["cve_fecha_informacion"],
        alias=["numero_transacciones", "txn", "movimientos"],
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
        filtros=["cve_fecha_informacion"],
        alias=[
            "importe",
            "monto_transacciones",
            "importe_neto",
            "monto_neto",
            "flujo_neto",
        ],
    ),
    Metrica(
        nombre="importe_bruto",
        descripcion="Suma del importe sin signo de las transacciones.",
        vista="vw_txn_firmada",
        agregacion="sum",
        columna="saldo_txn",
        unidad="MXN",
        dimensiones=[
            "cve_producto",
            "cve_txn",
            "cve_dominio",
            "cve_grupo",
            "fec_txn",
        ],
        filtros=["cve_fecha_informacion"],
        alias=["volumen", "monto_bruto", "flujo_bruto", "importe_sin_signo"],
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
