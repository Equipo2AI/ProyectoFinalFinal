"""Catálogo semántico ejecutable sobre la base sintética del MVP.

La capa de diseño ``capa_semantica_financiera_v1`` describe un modelo PostgreSQL
más amplio. Este módulo conserva sus conceptos útiles (métricas, reglas,
relaciones y glosario) pero los ancla a las tablas, vistas y columnas que sí
existen en ``banamex.db``.

El catálogo se construye por introspección y se enriquece con metadatos
curados. Así, si el esquema cambia, ``validar_catalogo`` lo detecta en lugar de
seguir respondiendo con nombres de tablas o columnas inexistentes.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from proyecto.agents.database import (
    COLUMNAS_SENSIBLES,
    create_readonly_engine,
    get_database_url,
)

VERSION_CATALOGO = 1

# ---------------------------------------------------------------------------
# Metadatos curados: tablas y vistas
# ---------------------------------------------------------------------------

TABLAS_NEGOCIO: dict[str, dict[str, Any]] = {
    "dim_fecha": {
        "nombre_negocio": "Dimensión de fechas",
        "descripcion": (
            "Calendario diario. Permite cortes por fecha, mes, trimestre y año; "
            "``cve_fecha`` y ``anio_mes`` son las llaves de periodo preferidas."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por día",
        "entidad": "Fecha",
        "fecha_referencia": "cve_fecha",
        "alias": ["calendario", "fechas", "periodo", "corte"],
        "preguntas": ["¿Qué meses cubre la base?", "¿Cómo se define el corte mensual?"],
        "confianza": "CONFIRMADO",
    },
    "dim_cliente": {
        "nombre_negocio": "Dimensión de clientes",
        "descripcion": (
            "Un registro por cliente. Contiene fechas de alta y nacimiento y las "
            "llaves hacia tipo de persona, género, segmento, giro y promotor."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por cliente (num_cte)",
        "entidad": "Cliente",
        "fecha_referencia": "fec_alta",
        "alias": ["clientes", "cliente", "cartera"],
        "preguntas": ["¿Cuántos clientes hay?", "¿Cómo se segmentan los clientes?"],
        "confianza": "CONFIRMADO",
    },
    "dim_cliente_persona": {
        "nombre_negocio": "Datos personales del cliente",
        "descripcion": (
            "Extensión con nombre, correo, teléfono, RFC y CURP. Es información "
            "personal: no debe devolverse a la UI ni usarse para métricas."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por cliente (num_cte)",
        "entidad": "Persona",
        "fecha_referencia": "",
        "alias": ["pii", "datos personales", "contacto"],
        "preguntas": [],
        "restricciones": "Solo metadatos o conteos; nunca valores completos.",
        "confianza": "CONFIRMADO",
    },
    "dim_cuenta": {
        "nombre_negocio": "Dimensión de cuentas",
        "descripcion": (
            "Una fila por cuenta. Es el puente obligatorio entre cliente, producto "
            "y los hechos de saldos o transacciones."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por cuenta (num_cta)",
        "entidad": "Cuenta",
        "fecha_referencia": "fec_alta",
        "alias": ["cuentas", "cuenta", "contrato"],
        "preguntas": ["¿Cuántas cuentas tiene un cliente?", "¿Qué producto tiene una cuenta?"],
        "confianza": "CONFIRMADO",
    },
    "dim_producto": {
        "nombre_negocio": "Dimensión de productos",
        "descripcion": (
            "Catálogo de productos con dominio, grupo y estatus. ``cve_grupo`` es "
            "la agrupación de producto realmente disponible; no existe "
            "``cve_subproducto`` en esta base."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por producto (cve_producto)",
        "entidad": "Producto",
        "fecha_referencia": "",
        "alias": ["productos", "producto", "portafolio"],
        "preguntas": ["¿Qué productos existen?", "¿Cómo se agrupan los productos?"],
        "confianza": "CONFIRMADO",
    },
    "dim_txn": {
        "nombre_negocio": "Catálogo de tipos de transacción",
        "descripcion": (
            "Define cada tipo de transacción por producto y su signo. La llave es "
            "compuesta: (cve_producto, cve_txn). ``signo`` = +1 aumenta saldo o "
            "deuda y -1 lo reduce."
        ),
        "tipo": "DIMENSION",
        "granularidad": "Una fila por (cve_producto, cve_txn)",
        "entidad": "Tipo de transacción",
        "fecha_referencia": "",
        "alias": ["tipos de transaccion", "catalogo txn", "signo"],
        "preguntas": ["¿Qué significa el signo de una transacción?"],
        "confianza": "CONFIRMADO",
    },
    "fact_saldos_captacoloca_m": {
        "nombre_negocio": "Hecho de saldos mensuales",
        "descripcion": (
            "Saldo de apertura y de cierre por cuenta y fecha de información. Es "
            "un snapshot: no se suman fechas distintas para un mismo saldo."
        ),
        "tipo": "FACT",
        "granularidad": "Una fila por (num_cta, cve_fecha_informacion)",
        "entidad": "Saldo",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["saldos", "saldo mensual", "captacion", "colocacion"],
        "preguntas": ["¿Cuál es el saldo al cierre?", "¿Cuál es el saldo por producto?"],
        "restricciones": "Filtrar un solo corte antes de sumar saldos.",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d": {
        "nombre_negocio": "Hecho de transacciones",
        "descripcion": (
            "Movimientos diarios por cuenta, producto y tipo de transacción. "
            "``saldo_txn`` es el importe sin signo; el signo se obtiene de "
            "``dim_txn`` o, preferentemente, de ``vw_txn_firmada``."
        ),
        "tipo": "FACT",
        "granularidad": "Una fila por (num_cta, cve_producto, cve_txn, cve_fecha_informacion)",
        "entidad": "Transacción",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["transacciones", "movimientos", "txn", "flujo"],
        "preguntas": ["¿Cuántas transacciones hubo?", "¿Cuál es el flujo neto?"],
        "restricciones": "Usar ``vw_txn_firmada`` para importes netos.",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico": {
        "nombre_negocio": "Vista pública de clientes",
        "descripcion": (
            "Clientes con segmento, género, giro, promotor, sucursal, territorio y "
            "dirección ya resueltos. Excluye datos personales y es la vista "
            "preferida para conteos y agrupaciones de clientes."
        ),
        "tipo": "VISTA",
        "granularidad": "Una fila por cliente (num_cte)",
        "entidad": "Cliente",
        "fecha_referencia": "fec_alta",
        "alias": ["clientes publicos", "vista clientes", "cartera publica"],
        "preguntas": ["¿Cuántos clientes hay por segmento?", "¿Cómo se distribuyen por territorio?"],
        "restricciones": "No expone nombre, correo, teléfono, RFC ni CURP.",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual": {
        "nombre_negocio": "Vista de saldos mensuales",
        "descripcion": (
            "Une saldos con cuenta, producto y calendario. Expone cliente, "
            "producto, dominio, grupo, fecha de corte y saldo del mes anterior. "
            "Es la vista preferida para saldos."
        ),
        "tipo": "VISTA",
        "granularidad": "Una fila por (num_cta, cve_fecha_informacion)",
        "entidad": "Saldo",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["saldos mensuales", "vista saldos", "saldo por producto"],
        "preguntas": ["¿Cuál es el saldo por segmento?", "¿Cómo cambió el saldo mes a mes?"],
        "restricciones": "Elegir un solo corte para saldos de cierre o apertura.",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada": {
        "nombre_negocio": "Vista de transacciones firmadas",
        "descripcion": (
            "Une transacciones con cuenta, producto y catálogo de transacción. "
            "``importe_firmado`` ya aplica el signo; ``saldo_txn`` conserva el "
            "importe bruto."
        ),
        "tipo": "VISTA",
        "granularidad": "Una fila por transacción",
        "entidad": "Transacción",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["transacciones firmadas", "vista txn", "flujo neto"],
        "preguntas": ["¿Cuál es el importe neto?", "¿Cuál es el importe bruto?"],
        "restricciones": "No sumar ``saldo_txn`` como si ya tuviera signo.",
        "confianza": "CONFIRMADO",
    },
    "raw_clientes": {
        "nombre_negocio": "Capa cruda de clientes",
        "descripcion": (
            "Datos de clientes con defectos intencionales para probar calidad. "
            "No debe usarse para métricas de negocio ni para responder consultas "
            "transaccionales."
        ),
        "tipo": "RAW",
        "granularidad": "Una fila por registro crudo",
        "entidad": "Cliente",
        "fecha_referencia": "fec_alta",
        "alias": ["crudo clientes", "clientes raw"],
        "preguntas": ["¿Qué problemas de calidad tiene raw_clientes?"],
        "restricciones": "Solo conteos y metadatos; contiene PII.",
        "confianza": "CONFIRMADO",
    },
    "raw_saldos": {
        "nombre_negocio": "Capa cruda de saldos",
        "descripcion": (
            "Saldos sin normalizar con defectos intencionales. Se usa para "
            "auditoría de calidad, no para métricas de negocio."
        ),
        "tipo": "RAW",
        "granularidad": "Una fila por registro crudo",
        "entidad": "Saldo",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["crudo saldos", "saldos raw"],
        "preguntas": ["¿Qué problemas de calidad tiene raw_saldos?"],
        "restricciones": "Solo conteos y metadatos.",
        "confianza": "CONFIRMADO",
    },
    "raw_transacciones": {
        "nombre_negocio": "Capa cruda de transacciones",
        "descripcion": (
            "Transacciones sin normalizar con defectos intencionales. Se usa para "
            "auditoría de calidad, no para métricas de negocio."
        ),
        "tipo": "RAW",
        "granularidad": "Una fila por registro crudo",
        "entidad": "Transacción",
        "fecha_referencia": "cve_fecha_informacion",
        "alias": ["crudo transacciones", "txn raw"],
        "preguntas": ["¿Qué problemas de calidad tiene raw_transacciones?"],
        "restricciones": "Solo conteos y metadatos.",
        "confianza": "CONFIRMADO",
    },
    "calidad_hallazgos_esperados": {
        "nombre_negocio": "Hallazgos esperados de calidad",
        "descripcion": (
            "Catálogo de defectos sembrados y su filas afectadas esperadas. "
            "Sirve para contrastar la auditoría del Data Steward."
        ),
        "tipo": "CALIDAD",
        "granularidad": "Una fila por hallazgo",
        "entidad": "Hallazgo de calidad",
        "fecha_referencia": "",
        "alias": ["calidad", "hallazgos", "defectos"],
        "preguntas": ["¿Qué defectos se sembraron?"],
        "confianza": "CONFIRMADO",
    },
}

# Metadatos curados de columnas. Las columnas no listadas se describen por
# introspección para no duplicar el esquema completo en el código.
COLUMNAS_NEGOCIO: dict[str, dict[str, Any]] = {
    # Cliente
    "dim_cliente.num_cte": {
        "nombre_negocio": "Número de cliente",
        "descripcion": "Identificador interno del cliente.",
        "sinonimos": ["cliente", "id cliente", "num cliente"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.fec_alta": {
        "nombre_negocio": "Fecha de alta",
        "descripcion": "Fecha en que el cliente se dio de alta.",
        "sinonimos": ["alta", "fecha alta", "antigüedad"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.fec_nac": {
        "nombre_negocio": "Fecha de nacimiento",
        "descripcion": "Fecha de nacimiento del cliente; dato personal.",
        "sinonimos": ["nacimiento", "edad"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.cve_tipo_persona": {
        "nombre_negocio": "Tipo de persona",
        "descripcion": "Llave hacia física o moral.",
        "sinonimos": ["tipo persona", "persona fisica", "persona moral"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.cve_genero": {
        "nombre_negocio": "Género",
        "descripcion": "Llave hacia el catálogo de género.",
        "sinonimos": ["genero", "sexo"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.cve_segmento": {
        "nombre_negocio": "Segmento",
        "descripcion": "Segmento comercial del cliente.",
        "sinonimos": ["segmento", "segmentacion"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.cve_giro": {
        "nombre_negocio": "Giro",
        "descripcion": "Giro o actividad económica del cliente.",
        "sinonimos": ["giro", "actividad", "industria"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente.cve_promotor": {
        "nombre_negocio": "Promotor",
        "descripcion": "Promotor responsable de la relación con el cliente.",
        "sinonimos": ["promotor", "asesor"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Cuenta
    "dim_cuenta.num_cta": {
        "nombre_negocio": "Número de cuenta",
        "descripcion": "Identificador interno de la cuenta.",
        "sinonimos": ["cuenta", "id cuenta", "num cuenta", "contrato"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cuenta.num_cte": {
        "nombre_negocio": "Número de cliente",
        "descripcion": "Cliente dueño de la cuenta.",
        "sinonimos": ["cliente", "titular"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cuenta.cve_producto": {
        "nombre_negocio": "Producto",
        "descripcion": "Producto de la cuenta.",
        "sinonimos": ["producto", "tipo producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cuenta.fec_alta": {
        "nombre_negocio": "Fecha de alta de la cuenta",
        "descripcion": "Fecha de apertura de la cuenta.",
        "sinonimos": ["alta cuenta", "apertura"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_cuenta.cve_mes_alta": {
        "nombre_negocio": "Mes de alta",
        "descripcion": "Mes de apertura en formato AAAAMM.",
        "sinonimos": ["vintage", "mes alta", "antiguedad cuenta"],
        "tipo_semantico": "PERIODO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Producto
    "dim_producto.cve_producto": {
        "nombre_negocio": "Clave de producto",
        "descripcion": "Identificador del producto.",
        "sinonimos": ["producto", "clave producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_producto.cve_dominio": {
        "nombre_negocio": "Dominio",
        "descripcion": "Dominio de negocio del producto (por ejemplo captación o colocación).",
        "sinonimos": ["dominio", "negocio"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_producto.cve_grupo": {
        "nombre_negocio": "Grupo de producto",
        "descripcion": "Agrupación de producto disponible en esta base.",
        "sinonimos": ["grupo", "grupo producto", "subproducto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_producto.descripcion": {
        "nombre_negocio": "Descripción del producto",
        "descripcion": "Nombre legible del producto.",
        "sinonimos": ["nombre producto", "descripcion producto"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_producto.activo": {
        "nombre_negocio": "Producto activo",
        "descripcion": "Indica si el producto está vigente.",
        "sinonimos": ["activo", "vigente"],
        "tipo_semantico": "BOOLEANO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Tipo de transacción
    "dim_txn.cve_producto": {
        "nombre_negocio": "Producto",
        "descripcion": "Producto al que aplica el tipo de transacción.",
        "sinonimos": ["producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_txn.cve_txn": {
        "nombre_negocio": "Clave de transacción",
        "descripcion": "Tipo de transacción dentro del producto.",
        "sinonimos": ["tipo transaccion", "txn", "clave txn"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_txn.desc_txn": {
        "nombre_negocio": "Descripción de transacción",
        "descripcion": "Nombre legible del tipo de transacción.",
        "sinonimos": ["descripcion txn", "concepto"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_txn.signo": {
        "nombre_negocio": "Signo",
        "descripcion": "+1 aumenta saldo o deuda; -1 lo reduce.",
        "sinonimos": ["signo", "direccion", "naturaleza"],
        "tipo_semantico": "ENTERO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Fecha
    "dim_fecha.cve_fecha": {
        "nombre_negocio": "Clave de fecha",
        "descripcion": "Fecha en formato AAAAMMDD.",
        "sinonimos": ["fecha", "corte", "dia"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_fecha.anio_mes": {
        "nombre_negocio": "Año-mes",
        "descripcion": "Periodo mensual en formato AAAAMM.",
        "sinonimos": ["mes", "periodo", "anio mes"],
        "tipo_semantico": "PERIODO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_fecha.fecha": {
        "nombre_negocio": "Fecha",
        "descripcion": "Fecha legible del calendario.",
        "sinonimos": ["fecha"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "dim_fecha.es_fin_semana": {
        "nombre_negocio": "Fin de semana",
        "descripcion": "1 si la fecha es sábado o domingo; 0 en otro caso.",
        "sinonimos": ["fin de semana", "sabado", "domingo"],
        "tipo_semantico": "BOOLEANO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Hecho de saldos
    "fact_saldos_captacoloca_m.num_cta": {
        "nombre_negocio": "Cuenta",
        "descripcion": "Cuenta del saldo.",
        "sinonimos": ["cuenta"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_saldos_captacoloca_m.cve_fecha_informacion": {
        "nombre_negocio": "Fecha de información",
        "descripcion": "Corte mensual del saldo en formato AAAAMMDD.",
        "sinonimos": ["corte", "fecha corte", "periodo", "fecha informacion"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_saldos_captacoloca_m.saldo_apertura": {
        "nombre_negocio": "Saldo de apertura",
        "descripcion": "Saldo al inicio del periodo.",
        "sinonimos": ["apertura", "saldo inicial"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_saldos_captacoloca_m.saldo_mes": {
        "nombre_negocio": "Saldo de cierre",
        "descripcion": "Saldo al cierre del periodo.",
        "sinonimos": ["saldo", "saldo cierre", "saldo final", "saldo mes"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Hecho de transacciones
    "fact_txn_captacoloca_d.num_cta": {
        "nombre_negocio": "Cuenta",
        "descripcion": "Cuenta del movimiento.",
        "sinonimos": ["cuenta"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d.cve_producto": {
        "nombre_negocio": "Producto",
        "descripcion": "Producto del movimiento.",
        "sinonimos": ["producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d.cve_txn": {
        "nombre_negocio": "Tipo de transacción",
        "descripcion": "Tipo de transacción dentro del producto.",
        "sinonimos": ["txn", "tipo transaccion"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d.fec_txn": {
        "nombre_negocio": "Fecha de transacción",
        "descripcion": "Fecha en que ocurrió el movimiento.",
        "sinonimos": ["fecha txn", "fecha movimiento", "dia"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d.cve_fecha_informacion": {
        "nombre_negocio": "Fecha de información",
        "descripcion": "Fecha de corte o información del movimiento.",
        "sinonimos": ["corte", "fecha informacion"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "fact_txn_captacoloca_d.saldo_txn": {
        "nombre_negocio": "Importe bruto",
        "descripcion": "Importe del movimiento sin aplicar el signo.",
        "sinonimos": ["importe", "monto", "saldo txn", "flujo bruto"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # Vistas
    "vw_saldo_mensual.num_cta": {
        "nombre_negocio": "Cuenta",
        "descripcion": "Cuenta del saldo.",
        "sinonimos": ["cuenta"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.num_cte": {
        "nombre_negocio": "Cliente",
        "descripcion": "Cliente dueño de la cuenta.",
        "sinonimos": ["cliente", "titular"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.cve_producto": {
        "nombre_negocio": "Producto",
        "descripcion": "Producto de la cuenta.",
        "sinonimos": ["producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.cve_dominio": {
        "nombre_negocio": "Dominio",
        "descripcion": "Dominio del producto.",
        "sinonimos": ["dominio", "negocio"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.cve_grupo": {
        "nombre_negocio": "Grupo de producto",
        "descripcion": "Grupo del producto.",
        "sinonimos": ["grupo", "grupo producto"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.cve_fecha_informacion": {
        "nombre_negocio": "Fecha de información",
        "descripcion": "Corte del saldo en formato AAAAMMDD.",
        "sinonimos": ["corte", "fecha corte", "periodo"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.fec_corte": {
        "nombre_negocio": "Fecha de corte",
        "descripcion": "Fecha legible del corte.",
        "sinonimos": ["fecha corte", "fecha"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.anio_mes": {
        "nombre_negocio": "Año-mes",
        "descripcion": "Periodo mensual en formato AAAAMM.",
        "sinonimos": ["mes", "periodo", "anio mes"],
        "tipo_semantico": "PERIODO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.saldo_apertura": {
        "nombre_negocio": "Saldo de apertura",
        "descripcion": "Saldo al inicio del periodo.",
        "sinonimos": ["apertura", "saldo inicial"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.saldo_mes": {
        "nombre_negocio": "Saldo de cierre",
        "descripcion": "Saldo al cierre del periodo.",
        "sinonimos": ["saldo", "saldo cierre", "saldo final", "saldo mes"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_saldo_mensual.saldo_mes_anterior": {
        "nombre_negocio": "Saldo del mes anterior",
        "descripcion": "Saldo de cierre del corte anterior de la misma cuenta.",
        "sinonimos": ["saldo anterior", "mes anterior", "variacion"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.num_cta": {
        "nombre_negocio": "Cuenta",
        "descripcion": "Cuenta del movimiento.",
        "sinonimos": ["cuenta"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.num_cte": {
        "nombre_negocio": "Cliente",
        "descripcion": "Cliente dueño de la cuenta.",
        "sinonimos": ["cliente"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.saldo_txn": {
        "nombre_negocio": "Importe bruto",
        "descripcion": "Importe del movimiento sin signo.",
        "sinonimos": ["importe bruto", "monto bruto", "flujo bruto"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.importe_firmado": {
        "nombre_negocio": "Importe firmado",
        "descripcion": "Importe del movimiento con signo aplicado.",
        "sinonimos": ["importe neto", "monto neto", "flujo neto"],
        "tipo_semantico": "MONEDA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.signo": {
        "nombre_negocio": "Signo",
        "descripcion": "+1 aumenta saldo o deuda; -1 lo reduce.",
        "sinonimos": ["signo", "direccion"],
        "tipo_semantico": "ENTERO",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_txn_firmada.fec_txn": {
        "nombre_negocio": "Fecha de transacción",
        "descripcion": "Fecha del movimiento.",
        "sinonimos": ["fecha txn", "fecha movimiento"],
        "tipo_semantico": "FECHA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.num_cte": {
        "nombre_negocio": "Cliente",
        "descripcion": "Identificador interno del cliente.",
        "sinonimos": ["cliente", "num cliente"],
        "tipo_semantico": "IDENTIFICADOR",
        "sensibilidad": "IDENTIFICADOR_INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.segmento": {
        "nombre_negocio": "Segmento",
        "descripcion": "Segmento comercial ya resuelto.",
        "sinonimos": ["segmento", "segmentacion"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.genero": {
        "nombre_negocio": "Género",
        "descripcion": "Género ya resuelto.",
        "sinonimos": ["genero", "sexo"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.tipo_persona": {
        "nombre_negocio": "Tipo de persona",
        "descripcion": "Física o moral.",
        "sinonimos": ["tipo persona", "persona fisica", "persona moral"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.territorio": {
        "nombre_negocio": "Territorio",
        "descripcion": "Territorio de la sucursal del cliente.",
        "sinonimos": ["territorio", "region"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.sucursal": {
        "nombre_negocio": "Sucursal",
        "descripcion": "Sucursal del promotor del cliente.",
        "sinonimos": ["sucursal", "oficina"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    "vw_cliente_publico.promotor": {
        "nombre_negocio": "Promotor",
        "descripcion": "Nombre del promotor.",
        "sinonimos": ["promotor", "asesor"],
        "tipo_semantico": "CATEGORIA",
        "sensibilidad": "INTERNO",
        "confianza": "CONFIRMADO",
    },
    # PII
    "dim_cliente_persona.nombre": {
        "nombre_negocio": "Nombre",
        "descripcion": "Nombre completo del cliente; dato personal.",
        "sinonimos": ["nombre", "cliente"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente_persona.correo": {
        "nombre_negocio": "Correo",
        "descripcion": "Correo electrónico; dato personal.",
        "sinonimos": ["correo", "email"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente_persona.telefono": {
        "nombre_negocio": "Teléfono",
        "descripcion": "Teléfono de contacto; dato personal.",
        "sinonimos": ["telefono", "celular"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente_persona.rfc": {
        "nombre_negocio": "RFC",
        "descripcion": "Registro federal de contribuyentes; dato personal.",
        "sinonimos": ["rfc"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
    "dim_cliente_persona.curp": {
        "nombre_negocio": "CURP",
        "descripcion": "Clave única de registro de población; dato personal.",
        "sinonimos": ["curp"],
        "tipo_semantico": "TEXTO",
        "sensibilidad": "PII",
        "confianza": "CONFIRMADO",
    },
}

# ---------------------------------------------------------------------------
# Métricas, reglas, relaciones y glosario externo
# ---------------------------------------------------------------------------

METRICAS: tuple[dict[str, Any], ...] = (
    {
        "codigo": "M001",
        "nombre": "saldo_total",
        "nombre_negocio": "Saldo de cierre",
        "descripcion": "Suma del saldo de cierre en un solo corte.",
        "vista": "vw_saldo_mensual",
        "formula": "SUM(saldo_mes)",
        "agregacion": "sum",
        "columna": "saldo_mes",
        "unidad": "MXN",
        "tipo": "snapshot",
        "dimensiones": ["anio_mes", "cve_producto", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": ["cve_fecha_informacion"],
        "sinonimos": ["saldo", "saldo total", "saldo mensual", "saldo de cierre", "saldo_cierre"],
        "restricciones": "Elegir un solo cve_fecha_informacion antes de agregar.",
        "columnas_validadas": ["saldo_mes", "cve_fecha_informacion", "anio_mes"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M002",
        "nombre": "saldo_apertura",
        "nombre_negocio": "Saldo de apertura",
        "descripcion": "Suma del saldo de apertura en un solo corte.",
        "vista": "vw_saldo_mensual",
        "formula": "SUM(saldo_apertura)",
        "agregacion": "sum",
        "columna": "saldo_apertura",
        "unidad": "MXN",
        "tipo": "snapshot",
        "dimensiones": ["anio_mes", "cve_producto", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": ["cve_fecha_informacion"],
        "sinonimos": ["saldo inicial", "apertura"],
        "restricciones": "Elegir un solo cve_fecha_informacion antes de agregar.",
        "columnas_validadas": ["saldo_apertura", "cve_fecha_informacion"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M003",
        "nombre": "saldo_promedio",
        "nombre_negocio": "Saldo promedio",
        "descripcion": "Promedio del saldo de cierre en un solo corte.",
        "vista": "vw_saldo_mensual",
        "formula": "AVG(saldo_mes)",
        "agregacion": "avg",
        "columna": "saldo_mes",
        "unidad": "MXN",
        "tipo": "snapshot",
        "dimensiones": ["anio_mes", "cve_producto", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": ["cve_fecha_informacion"],
        "sinonimos": ["promedio saldo", "saldo medio"],
        "restricciones": "No promediar cortes distintos como si fueran una serie aditiva.",
        "columnas_validadas": ["saldo_mes", "cve_fecha_informacion"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M004",
        "nombre": "importe_bruto",
        "nombre_negocio": "Importe bruto de transacciones",
        "descripcion": "Suma del importe sin signo de las transacciones.",
        "vista": "vw_txn_firmada",
        "formula": "SUM(saldo_txn)",
        "agregacion": "sum",
        "columna": "saldo_txn",
        "unidad": "MXN",
        "tipo": "flujo",
        "dimensiones": ["fec_txn", "cve_producto", "cve_txn", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": [],
        "sinonimos": ["importe bruto", "monto bruto", "flujo bruto", "volumen"],
        "restricciones": "No confundir con importe neto; no aplicar signo dos veces.",
        "columnas_validadas": ["saldo_txn", "fec_txn"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M005",
        "nombre": "importe_transacciones",
        "nombre_negocio": "Importe neto de transacciones",
        "descripcion": "Suma del importe firmado de las transacciones.",
        "vista": "vw_txn_firmada",
        "formula": "SUM(importe_firmado)",
        "agregacion": "sum",
        "columna": "importe_firmado",
        "unidad": "MXN",
        "tipo": "flujo",
        "dimensiones": ["fec_txn", "cve_producto", "cve_txn", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": [],
        "sinonimos": ["importe neto", "monto neto", "flujo neto", "importe transacciones", "importe_neto"],
        "restricciones": "La vista ya aplica el signo de dim_txn.",
        "columnas_validadas": ["importe_firmado", "fec_txn"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M006",
        "nombre": "clientes",
        "nombre_negocio": "Clientes",
        "descripcion": "Número de clientes distintos.",
        "vista": "vw_cliente_publico",
        "formula": "COUNT(DISTINCT num_cte)",
        "agregacion": "count_distinct",
        "columna": "num_cte",
        "unidad": "clientes",
        "tipo": "conteo",
        "dimensiones": [
            "segmento",
            "genero",
            "tipo_persona",
            "giro",
            "territorio",
            "sucursal",
            "cve_segmento",
            "cve_promotor",
        ],
        "filtros_obligatorios": [],
        "sinonimos": ["numero clientes", "total clientes", "clientes distintos"],
        "restricciones": "Contar clientes distintos; no contar cuentas.",
        "columnas_validadas": ["num_cte", "segmento", "genero", "territorio"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M007",
        "nombre": "cuentas",
        "nombre_negocio": "Cuentas",
        "descripcion": "Número de cuentas distintas.",
        "vista": "vw_saldo_mensual",
        "formula": "COUNT(DISTINCT num_cta)",
        "agregacion": "count_distinct",
        "columna": "num_cta",
        "unidad": "cuentas",
        "tipo": "conteo",
        "dimensiones": ["anio_mes", "cve_producto", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": [],
        "sinonimos": ["numero cuentas", "total cuentas", "cuentas distintas"],
        "restricciones": "Contar cuentas distintas; no confundir con clientes.",
        "columnas_validadas": ["num_cta", "anio_mes"],
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "M008",
        "nombre": "transacciones",
        "nombre_negocio": "Transacciones",
        "descripcion": "Número de transacciones.",
        "vista": "vw_txn_firmada",
        "formula": "COUNT(*)",
        "agregacion": "count",
        "columna": "num_cta",
        "unidad": "transacciones",
        "tipo": "conteo",
        "dimensiones": ["fec_txn", "cve_producto", "cve_txn", "cve_dominio", "cve_grupo"],
        "filtros_obligatorios": [],
        "sinonimos": ["numero transacciones", "txn", "movimientos"],
        "restricciones": "No sumar importes al contar transacciones.",
        "columnas_validadas": ["num_cta", "fec_txn", "cve_txn"],
        "confianza": "CONFIRMADO",
    },
)

REGLAS: tuple[dict[str, Any], ...] = (
    {
        "codigo": "R001",
        "nombre": "Un solo corte para saldos",
        "descripcion": (
            "Los saldos de cierre y apertura son snapshots. Antes de agregarlos "
            "se debe fijar un único cve_fecha_informacion o anio_mes."
        ),
        "ambito": "METRICA",
        "tabla": "vw_saldo_mensual",
        "campo": "cve_fecha_informacion",
        "tipo": "AGREGACION",
        "instruccion": "Filtrar un solo corte; no sumar cortes distintos.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R002",
        "nombre": "Clientes distintos",
        "descripcion": "Un cliente puede tener varias cuentas; contar clientes con COUNT(DISTINCT num_cte).",
        "ambito": "METRICA",
        "tabla": "vw_cliente_publico",
        "campo": "num_cte",
        "tipo": "CONTEO",
        "instruccion": "Usar COUNT(DISTINCT num_cte), no COUNT(*).",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R003",
        "nombre": "Cuentas distintas",
        "descripcion": "Una cuenta aparece en varios cortes; contar cuentas con COUNT(DISTINCT num_cta).",
        "ambito": "METRICA",
        "tabla": "vw_saldo_mensual",
        "campo": "num_cta",
        "tipo": "CONTEO",
        "instruccion": "Usar COUNT(DISTINCT num_cta), no COUNT(*).",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R004",
        "nombre": "Fecha de movimiento vs fecha de información",
        "descripcion": (
            "``fec_txn`` es la fecha del movimiento; ``cve_fecha_informacion`` "
            "es el corte o periodo de información. No son intercambiables."
        ),
        "ambito": "CAMPO",
        "tabla": "vw_txn_firmada",
        "campo": "fec_txn",
        "tipo": "FILTRO",
        "instruccion": "Para movimientos por día usar fec_txn; para cortes usar cve_fecha_informacion.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R005",
        "nombre": "Relaciones por dimensiones",
        "descripcion": (
            "Los hechos se unen a cliente y producto mediante dim_cuenta. No hay "
            "llave directa de cliente en fact_txn_captacoloca_d."
        ),
        "ambito": "RELACION",
        "tabla": "fact_txn_captacoloca_d",
        "campo": "num_cta",
        "tipo": "JOIN",
        "instruccion": "Unir hechos con dim_cuenta y luego con dim_cliente o dim_producto.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R006",
        "nombre": "Signo de transacción",
        "descripcion": "``dim_txn.signo`` define si el movimiento aumenta o reduce saldo o deuda.",
        "ambito": "CAMPO",
        "tabla": "vw_txn_firmada",
        "campo": "importe_firmado",
        "tipo": "CALCULO",
        "instruccion": "Para importe neto usar importe_firmado; para bruto usar saldo_txn.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R007",
        "nombre": "Llave compuesta de dim_txn",
        "descripcion": "dim_txn se identifica por (cve_producto, cve_txn), no solo por cve_txn.",
        "ambito": "RELACION",
        "tabla": "dim_txn",
        "campo": "cve_txn",
        "tipo": "JOIN",
        "instruccion": "Unir con ambas columnas: cve_producto y cve_txn.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R008",
        "nombre": "Formato de fechas",
        "descripcion": "cve_fecha y cve_fecha_informacion usan AAAAMMDD; anio_mes usa AAAAMM.",
        "ambito": "CAMPO",
        "tabla": "dim_fecha",
        "campo": "cve_fecha",
        "tipo": "FORMATO",
        "instruccion": "No comparar fechas ISO con enteros AAAAMMDD sin convertir.",
        "prioridad": 2,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R009",
        "nombre": "Vistas preferidas",
        "descripcion": (
            "Para preguntas de negocio usar vw_saldo_mensual, vw_txn_firmada y "
            "vw_cliente_publico; evitan joins incompletos y aplican el signo."
        ),
        "ambito": "TABLA",
        "tabla": "vw_saldo_mensual",
        "campo": "",
        "tipo": "USO",
        "instruccion": "Preferir las vistas públicas sobre hechos crudos.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R010",
        "nombre": "Capa cruda solo para calidad",
        "descripcion": "raw_* y calidad_hallazgos_esperados son para auditoría, no para métricas de negocio.",
        "ambito": "TABLA",
        "tabla": "raw_clientes",
        "campo": "",
        "tipo": "USO",
        "instruccion": "No responder saldos, clientes o transacciones desde raw_*.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R011",
        "nombre": "Datos personales",
        "descripcion": "dim_cliente_persona contiene PII; solo se permiten conteos o metadatos.",
        "ambito": "TABLA",
        "tabla": "dim_cliente_persona",
        "campo": "nombre",
        "tipo": "PRIVACIDAD",
        "instruccion": "No devolver nombre, correo, teléfono, RFC ni CURP.",
        "prioridad": 1,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
    {
        "codigo": "R012",
        "nombre": "Periodo obligatorio para series",
        "descripcion": "Toda serie temporal debe declarar el periodo o corte que se está usando.",
        "ambito": "METRICA",
        "tabla": "vw_saldo_mensual",
        "campo": "anio_mes",
        "tipo": "FILTRO",
        "instruccion": "Si la pregunta no trae periodo, pedirlo o declarar el supuesto.",
        "prioridad": 2,
        "obligatoria": True,
        "confianza": "CONFIRMADO",
    },
)

RELACIONES: tuple[dict[str, Any], ...] = (
    {
        "nombre": "fact_saldos_a_cuenta",
        "origen": "fact_saldos_captacoloca_m",
        "campos_origen": ["num_cta"],
        "destino": "dim_cuenta",
        "campos_destino": ["num_cta"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada saldo pertenece a una cuenta existente.",
    },
    {
        "nombre": "fact_saldos_a_fecha",
        "origen": "fact_saldos_captacoloca_m",
        "campos_origen": ["cve_fecha_informacion"],
        "destino": "dim_fecha",
        "campos_destino": ["cve_fecha"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada corte de saldo existe en el calendario.",
    },
    {
        "nombre": "fact_txn_a_cuenta",
        "origen": "fact_txn_captacoloca_d",
        "campos_origen": ["num_cta"],
        "destino": "dim_cuenta",
        "campos_destino": ["num_cta"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada movimiento pertenece a una cuenta existente.",
    },
    {
        "nombre": "fact_txn_a_producto",
        "origen": "fact_txn_captacoloca_d",
        "campos_origen": ["cve_producto"],
        "destino": "dim_producto",
        "campos_destino": ["cve_producto"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada movimiento usa un producto existente.",
    },
    {
        "nombre": "fact_txn_a_dim_txn",
        "origen": "fact_txn_captacoloca_d",
        "campos_origen": ["cve_producto", "cve_txn"],
        "destino": "dim_txn",
        "campos_destino": ["cve_producto", "cve_txn"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "El tipo de transacción se resuelve con la llave compuesta producto + txn.",
    },
    {
        "nombre": "fact_txn_a_fecha",
        "origen": "fact_txn_captacoloca_d",
        "campos_origen": ["cve_fecha_informacion"],
        "destino": "dim_fecha",
        "campos_destino": ["cve_fecha"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada corte de transacción existe en el calendario.",
    },
    {
        "nombre": "cuenta_a_cliente",
        "origen": "dim_cuenta",
        "campos_origen": ["num_cte"],
        "destino": "dim_cliente",
        "campos_destino": ["num_cte"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cuenta pertenece a un cliente.",
    },
    {
        "nombre": "cuenta_a_producto",
        "origen": "dim_cuenta",
        "campos_origen": ["cve_producto"],
        "destino": "dim_producto",
        "campos_destino": ["cve_producto"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cuenta tiene un producto.",
    },
    {
        "nombre": "cliente_a_tipo_persona",
        "origen": "dim_cliente",
        "campos_origen": ["cve_tipo_persona"],
        "destino": "dim_tipo_persona",
        "campos_destino": ["cve_tipo_persona"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cliente tiene un tipo de persona.",
    },
    {
        "nombre": "cliente_a_genero",
        "origen": "dim_cliente",
        "campos_origen": ["cve_genero"],
        "destino": "dim_genero",
        "campos_destino": ["cve_genero"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cliente tiene un género.",
    },
    {
        "nombre": "cliente_a_segmento",
        "origen": "dim_cliente",
        "campos_origen": ["cve_segmento"],
        "destino": "dim_segmento",
        "campos_destino": ["cve_segmento"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cliente tiene un segmento.",
    },
    {
        "nombre": "cliente_a_giro",
        "origen": "dim_cliente",
        "campos_origen": ["cve_giro"],
        "destino": "dim_giro",
        "campos_destino": ["cve_giro"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cliente tiene un giro.",
    },
    {
        "nombre": "cliente_a_promotor",
        "origen": "dim_cliente",
        "campos_origen": ["cve_promotor"],
        "destino": "dim_promotor",
        "campos_destino": ["cve_promotor"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada cliente tiene un promotor.",
    },
    {
        "nombre": "producto_a_dominio",
        "origen": "dim_producto",
        "campos_origen": ["cve_dominio"],
        "destino": "dim_dominio",
        "campos_destino": ["cve_dominio"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada producto tiene un dominio.",
    },
    {
        "nombre": "producto_a_grupo",
        "origen": "dim_producto",
        "campos_origen": ["cve_grupo"],
        "destino": "dim_grupo_producto",
        "campos_destino": ["cve_grupo"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada producto tiene un grupo.",
    },
    {
        "nombre": "promotor_a_sucursal",
        "origen": "dim_promotor",
        "campos_origen": ["cve_sucursal"],
        "destino": "dim_sucursal",
        "campos_destino": ["cve_sucursal"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada promotor pertenece a una sucursal.",
    },
    {
        "nombre": "sucursal_a_territorio",
        "origen": "dim_sucursal",
        "campos_origen": ["cve_territorio"],
        "destino": "dim_territorio",
        "campos_destino": ["cve_territorio"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada sucursal pertenece a un territorio.",
    },
    {
        "nombre": "territorio_a_subdireccion",
        "origen": "dim_territorio",
        "campos_origen": ["cve_subdireccion"],
        "destino": "dim_subdireccion",
        "campos_destino": ["cve_subdireccion"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada territorio pertenece a una subdirección.",
    },
    {
        "nombre": "subdireccion_a_direccion",
        "origen": "dim_subdireccion",
        "campos_origen": ["cve_direccion"],
        "destino": "dim_direccion",
        "campos_destino": ["cve_direccion"],
        "cardinalidad": "N:1",
        "validada": True,
        "verificar_datos": True,
        "descripcion": "Cada subdirección pertenece a una dirección.",
    },
)

GLOSARIO_EXTERNO: tuple[dict[str, Any], ...] = (
    {
        "variable": "VINTAGE",
        "descripcion": "Antigüedad de la cuenta o del cliente desde su alta.",
        "equivalente": "dim_cuenta.cve_mes_alta o dim_cliente.fec_alta",
        "aplica": "PARCIAL",
        "nota": "La base sintética no tiene una variable llamada VINTAGE; se deriva del mes o fecha de alta.",
    },
    {
        "variable": "PRODUCT",
        "descripcion": "Producto financiero.",
        "equivalente": "dim_producto.cve_producto / vw_saldo_mensual.cve_producto",
        "aplica": "SI",
        "nota": "El catálogo Base Cards usa una taxonomía distinta; no se fuerza el mapeo de subproductos.",
    },
    {
        "variable": "AGE_TAG",
        "descripcion": "Banda de antigüedad.",
        "equivalente": "Derivado de dim_cuenta.fec_alta o dim_cliente.fec_alta",
        "aplica": "PARCIAL",
        "nota": "No existe una columna AGE_TAG; se calcula con dim_fecha.",
    },
    {
        "variable": "ACCOUNTS",
        "descripcion": "Número de cuentas.",
        "equivalente": "vw_saldo_mensual.num_cta",
        "aplica": "SI",
        "nota": "Contar con COUNT(DISTINCT num_cta).",
    },
    {
        "variable": "ENRACCTS",
        "descripcion": "Cuentas con saldo o movimiento.",
        "equivalente": "vw_saldo_mensual.num_cta / vw_txn_firmada.num_cta",
        "aplica": "PARCIAL",
        "nota": "No hay una bandera ENRACCTS; se deriva de la existencia de saldo o transacción en el corte.",
    },
)


# ---------------------------------------------------------------------------
# Construcción del catálogo
# ---------------------------------------------------------------------------


def _normalizar(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", str(texto or "").casefold())
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _tipo_tabla(nombre: str, es_vista: bool) -> str:
    if es_vista or nombre.startswith("vw_"):
        return "VISTA"
    if nombre.startswith("raw_"):
        return "RAW"
    if nombre.startswith("calidad_"):
        return "CALIDAD"
    if nombre.startswith("dim_"):
        return "DIMENSION"
    if nombre.startswith("fact_"):
        return "FACT"
    return "TABLA"


def _tipo_semantico(
    columna: str,
    *,
    es_pk: bool,
    es_fk: bool,
) -> str:
    nombre = _normalizar(columna)
    if es_pk or es_fk or nombre in {"num_cte", "num_cta"}:
        return "IDENTIFICADOR"
    if nombre in {"fecha", "fec_txn", "fec_alta", "fec_nac", "fec_corte"}:
        return "FECHA"
    if nombre in {"cve_fecha", "cve_fecha_informacion"}:
        return "FECHA"
    if nombre in {"anio_mes", "cve_mes_alta"}:
        return "PERIODO"
    if any(p in nombre for p in ("saldo", "importe", "monto")):
        return "MONEDA"
    if nombre.startswith("num_") or nombre.endswith("_id"):
        return "IDENTIFICADOR"
    if nombre.startswith("cve_") or nombre.startswith("tipo_"):
        return "CATEGORIA"
    if nombre in {"activo", "es_fin_semana"}:
        return "BOOLEANO"
    if nombre in {"anio", "mes", "dia", "trimestre", "orden", "signo"}:
        return "ENTERO"
    return "TEXTO"


def _sensibilidad(tabla: str, columna: str) -> str:
    if tabla == "dim_cliente_persona":
        return "PII"
    if columna in COLUMNAS_SENSIBLES:
        return "PII"
    if tabla.startswith("raw_clientes") and columna in {
        "nombre",
        "correo",
        "telefono",
        "rfc",
        "curp",
        "fec_nac",
    }:
        return "PII"
    if columna in {"num_cte", "num_cta"}:
        return "IDENTIFICADOR_INTERNO"
    return "INTERNO"


def _nombre_legible(nombre: str) -> str:
    return nombre.replace("_", " ").strip().capitalize()


def _columnas_de(
    inspector,
    tabla: str,
    *,
    es_vista: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    columnas_fisicas = inspector.get_columns(tabla)
    pk = [] if es_vista else list(inspector.get_pk_constraint(tabla).get("constrained_columns") or [])
    fks = [] if es_vista else list(inspector.get_foreign_keys(tabla) or [])
    fk_por_columna: dict[str, dict[str, Any]] = {}
    for fk in fks:
        for origen, destino in zip(fk.get("constrained_columns") or [], fk.get("referred_columns") or []):
            fk_por_columna[origen] = {
                "tabla": fk.get("referred_table"),
                "columna": destino,
                "nombre": fk.get("name"),
            }

    columnas: list[dict[str, Any]] = []
    for columna in columnas_fisicas:
        nombre = columna["name"]
        es_pk = nombre in pk
        fk = fk_por_columna.get(nombre)
        clave = f"{tabla}.{nombre}"
        override = COLUMNAS_NEGOCIO.get(clave, {})
        columnas.append(
            {
                "nombre": nombre,
                "nombre_negocio": override.get("nombre_negocio", _nombre_legible(nombre)),
                "descripcion": override.get(
                    "descripcion",
                    f"Columna {nombre} de {tabla}.",
                ),
                "tipo_fisico": str(columna.get("type", "")),
                "tipo_semantico": override.get(
                    "tipo_semantico",
                    _tipo_semantico(nombre, es_pk=es_pk, es_fk=fk is not None),
                ),
                "nullable": bool(columna.get("nullable", True)),
                "pk": es_pk,
                "fk": fk,
                "sinonimos": list(override.get("sinonimos", [])),
                "sensibilidad": override.get("sensibilidad", _sensibilidad(tabla, nombre)),
                "confianza": override.get("confianza", "PROPUESTO"),
                "regla_validacion": override.get("regla_validacion", ""),
            }
        )
    return columnas, pk


def _metadatos_tabla(nombre: str, es_vista: bool) -> dict[str, Any]:
    tipo = _tipo_tabla(nombre, es_vista)
    override = TABLAS_NEGOCIO.get(nombre, {})
    return {
        "nombre": nombre,
        "nombre_negocio": override.get("nombre_negocio", _nombre_legible(nombre)),
        "descripcion": override.get(
            "descripcion",
            f"{tipo.capitalize()} de la base sintética.",
        ),
        "tipo": override.get("tipo", tipo),
        "granularidad": override.get("granularidad", ""),
        "entidad": override.get("entidad", ""),
        "fecha_referencia": override.get("fecha_referencia", ""),
        "alias": list(override.get("alias", [])),
        "preguntas": list(override.get("preguntas", [])),
        "restricciones": override.get("restricciones", ""),
        "confianza": override.get("confianza", "PROPUESTO"),
    }


def construir_catalogo(url_db: str | None = None) -> dict[str, Any]:
    """Construye el catálogo contra la base configurada.

    No modifica la base: usa un engine de solo lectura. El resultado es
    serializable a JSON y contiene tablas, vistas, columnas, métricas, reglas,
    relaciones y glosario externo.
    """
    engine = create_readonly_engine(url_db or get_database_url())
    try:
        inspector = inspect(engine)
        tablas = inspector.get_table_names()
        vistas = inspector.get_view_names()
        objetos: dict[str, Any] = {}
        for nombre in [*tablas, *vistas]:
            es_vista = nombre in vistas
            metadatos = _metadatos_tabla(nombre, es_vista)
            columnas, pk = _columnas_de(inspector, nombre, es_vista=es_vista)
            metadatos["pk"] = pk
            metadatos["columnas"] = columnas
            objetos[nombre] = metadatos

        base_datos = engine.url.database or ""
        if engine.dialect.name == "sqlite":
            base_datos = Path(base_datos).name

        return {
            "version": VERSION_CATALOGO,
            "motor": engine.dialect.name,
            "base_datos": base_datos,
            "tablas": objetos,
            "metricas": list(METRICAS),
            "reglas": list(REGLAS),
            "relaciones": list(RELACIONES),
            "glosario_externo": list(GLOSARIO_EXTERNO),
            "resumen": {
                "tablas": len(tablas),
                "vistas": len(vistas),
                "columnas": sum(len(t["columnas"]) for t in objetos.values()),
                "metricas": len(METRICAS),
                "reglas": len(REGLAS),
                "relaciones": len(RELACIONES),
            },
        }
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _error(errores: list[str], mensaje: str) -> None:
    errores.append(mensaje)


def _columnas_catalogo(tabla: dict[str, Any]) -> set[str]:
    return {columna["nombre"] for columna in tabla.get("columnas", [])}


def _validar_relacion(
    conexion,
    relacion: dict[str, Any],
    tablas: dict[str, Any],
    errores: list[str],
) -> None:
    origen = relacion.get("origen")
    destino = relacion.get("destino")
    campos_origen = list(relacion.get("campos_origen") or [])
    campos_destino = list(relacion.get("campos_destino") or [])

    if origen not in tablas:
        _error(errores, f"Relación {relacion.get('nombre')}: tabla origen {origen!r} no existe.")
        return
    if destino not in tablas:
        _error(errores, f"Relación {relacion.get('nombre')}: tabla destino {destino!r} no existe.")
        return
    if not campos_origen or len(campos_origen) != len(campos_destino):
        _error(errores, f"Relación {relacion.get('nombre')}: columnas de origen/destino incompletas.")
        return

    columnas_origen = _columnas_catalogo(tablas[origen])
    columnas_destino = _columnas_catalogo(tablas[destino])
    for campo in campos_origen:
        if campo not in columnas_origen:
            _error(errores, f"Relación {relacion.get('nombre')}: {origen}.{campo} no existe.")
            return
    for campo in campos_destino:
        if campo not in columnas_destino:
            _error(errores, f"Relación {relacion.get('nombre')}: {destino}.{campo} no existe.")
            return

    if not relacion.get("verificar_datos"):
        return

    # Se citan identificadores con comillas dobles. Los nombres vienen del
    # catálogo y de la introspección, no de texto libre del usuario.
    join = " AND ".join(
        f'o."{campo_o}" = d."{campo_d}"'
        for campo_o, campo_d in zip(campos_origen, campos_destino)
    )
    no_nulos = " AND ".join(f'o."{campo}" IS NOT NULL' for campo in campos_origen)
    nulos_destino = " AND ".join(f'd."{campo}" IS NULL' for campo in campos_destino)
    consulta = text(
        f'SELECT COUNT(*) FROM "{origen}" AS o '
        f'LEFT JOIN "{destino}" AS d ON {join} '
        f'WHERE {no_nulos} AND {nulos_destino}'
    )
    huerfanos = int(conexion.execute(consulta).scalar() or 0)
    if huerfanos:
        _error(
            errores,
            f"Relación {relacion.get('nombre')}: {huerfanos} filas huérfanas en {origen}.",
        )


def validar_catalogo(
    catalogo: dict[str, Any] | None = None,
    url_db: str | None = None,
) -> dict[str, Any]:
    """Compara el catálogo con la base real y verifica sus relaciones.

    Devuelve ``ok``, listas de errores/advertencias y un resumen. No lanza
    excepción por un desajuste: el comando de validación decide el código de
    salida.
    """
    catalogo = catalogo or construir_catalogo(url_db=url_db)
    errores: list[str] = []
    advertencias: list[str] = []
    tablas_catalogo = catalogo.get("tablas", {})

    engine = create_readonly_engine(url_db or get_database_url())
    try:
        inspector = inspect(engine)
        tablas_reales = set(inspector.get_table_names())
        vistas_reales = set(inspector.get_view_names())
        objetos_reales = tablas_reales | vistas_reales

        faltantes = sorted(objetos_reales - set(tablas_catalogo))
        sobrantes = sorted(set(tablas_catalogo) - objetos_reales)
        for nombre in faltantes:
            _error(errores, f"La base tiene {nombre!r} y el catálogo no lo describe.")
        for nombre in sobrantes:
            _error(errores, f"El catálogo describe {nombre!r}, pero la base no lo tiene.")

        for nombre in sorted(objetos_reales & set(tablas_catalogo)):
            tabla = tablas_catalogo[nombre]
            es_vista = nombre in vistas_reales
            columnas_reales = {
                columna["name"] for columna in inspector.get_columns(nombre)
            }
            columnas_catalogo = _columnas_catalogo(tabla)
            for columna in sorted(columnas_reales - columnas_catalogo):
                _error(errores, f"{nombre}: falta la columna {columna!r} en el catálogo.")
            for columna in sorted(columnas_catalogo - columnas_reales):
                _error(errores, f"{nombre}: el catálogo inventa la columna {columna!r}.")

            if not es_vista:
                pk_real = list(
                    inspector.get_pk_constraint(nombre).get("constrained_columns") or []
                )
                pk_catalogo = list(tabla.get("pk") or [])
                if set(pk_real) != set(pk_catalogo):
                    _error(
                        errores,
                        f"{nombre}: PK real {pk_real} distinta de la del catálogo {pk_catalogo}.",
                    )

        # Validar que las métricas apunten a objetos y columnas existentes.
        for metrica in catalogo.get("metricas", []):
            vista = metrica.get("vista")
            if vista not in objetos_reales:
                _error(errores, f"Métrica {metrica.get('codigo')}: vista {vista!r} no existe.")
                continue
            columnas = {
                columna["name"] for columna in inspector.get_columns(vista)
            }
            for columna in metrica.get("columnas_validadas", []):
                if columna not in columnas:
                    _error(
                        errores,
                        f"Métrica {metrica.get('codigo')}: columna {columna!r} no existe en {vista}.",
                    )
            columna_metrica = metrica.get("columna")
            if columna_metrica and columna_metrica not in columnas:
                _error(
                    errores,
                    f"Métrica {metrica.get('codigo')}: columna base {columna_metrica!r} no existe en {vista}.",
                )
            for columna in [
                *metrica.get("dimensiones", []),
                *metrica.get("filtros_obligatorios", []),
            ]:
                if columna not in columnas:
                    _error(
                        errores,
                        f"Métrica {metrica.get('codigo')}: dimensión/filtro {columna!r} no existe en {vista}.",
                    )
            formula = metrica.get("formula")
            if formula:
                try:
                    with engine.connect() as conexion:
                        conexion.execute(
                            text(f'SELECT {formula} FROM "{vista}" LIMIT 0')
                        )
                except Exception as exc:  # noqa: BLE001 - se reporta como validación
                    _error(
                        errores,
                        f"Métrica {metrica.get('codigo')}: la fórmula {formula!r} no es válida en {vista}: {exc}",
                    )

        # Validar también el catálogo que usa el Data Analyst en runtime.
        # Así el catálogo semántico y las métricas certificadas no divergen.
        metricas_runtime_total = 0
        try:
            from proyecto.metrics import METRICAS as METRICAS_RUNTIME
        except Exception as exc:  # noqa: BLE001 - se reporta como advertencia
            advertencias.append(
                f"No se pudo cargar proyecto.metrics para validar métricas runtime: {exc}"
            )
        else:
            metricas_runtime_total = len(METRICAS_RUNTIME)
            nombres_semanticos = {
                str(metrica.get("nombre"))
                for metrica in catalogo.get("metricas", [])
                if metrica.get("nombre")
            }
            nombres_runtime = {metrica.nombre for metrica in METRICAS_RUNTIME}
            for nombre in sorted(nombres_semanticos - nombres_runtime):
                advertencias.append(
                    f"La métrica semántica {nombre!r} no existe en proyecto.metrics."
                )
            for nombre in sorted(nombres_runtime - nombres_semanticos):
                advertencias.append(
                    f"La métrica runtime {nombre!r} no existe en el catálogo semántico."
                )

            semanticas_por_nombre = {
                str(metrica.get("nombre")): metrica
                for metrica in catalogo.get("metricas", [])
                if metrica.get("nombre")
            }
            for metrica in METRICAS_RUNTIME:
                semantica = semanticas_por_nombre.get(metrica.nombre)
                if semantica and str(semantica.get("tipo", "")).lower() != metrica.tipo:
                    _error(
                        errores,
                        f"Métrica {metrica.nombre}: tipo semántico "
                        f"{semantica.get('tipo')!r} distinto del runtime {metrica.tipo!r}.",
                    )
                if metrica.vista not in objetos_reales:
                    _error(
                        errores,
                        f"Métrica runtime {metrica.nombre}: vista {metrica.vista!r} no existe.",
                    )
                    continue
                columnas = {
                    columna["name"] for columna in inspector.get_columns(metrica.vista)
                }
                requeridas = {
                    metrica.columna,
                    *metrica.dimensiones,
                    *metrica.filtros,
                }
                for columna in sorted(requeridas - columnas):
                    _error(
                        errores,
                        f"Métrica runtime {metrica.nombre}: columna {columna!r} no existe en {metrica.vista}.",
                    )
                if metrica.agregacion not in {"count", "count_distinct", "sum", "avg"}:
                    _error(
                        errores,
                        f"Métrica runtime {metrica.nombre}: agregación {metrica.agregacion!r} no soportada.",
                    )

        # Validar reglas que declaran tabla/campo.
        for regla in catalogo.get("reglas", []):
            tabla = regla.get("tabla")
            if tabla and tabla not in objetos_reales:
                _error(errores, f"Regla {regla.get('codigo')}: tabla {tabla!r} no existe.")
                continue
            campo = regla.get("campo")
            if tabla and campo:
                columnas = {
                    columna["name"] for columna in inspector.get_columns(tabla)
                }
                if campo not in columnas:
                    _error(
                        errores,
                        f"Regla {regla.get('codigo')}: campo {tabla}.{campo} no existe.",
                    )

        # Validar relaciones y, si aplica, integridad referencial.
        with engine.connect() as conexion:
            for relacion in catalogo.get("relaciones", []):
                _validar_relacion(conexion, relacion, tablas_catalogo, errores)

        # Comprobaciones estructurales adicionales.
        with engine.connect() as conexion:
            duplicados_txn = int(
                conexion.execute(
                    text(
                        "SELECT COUNT(*) FROM ("
                        "SELECT cve_producto, cve_txn, COUNT(*) AS n "
                        "FROM dim_txn GROUP BY cve_producto, cve_txn HAVING COUNT(*) > 1"
                        ")"
                    )
                ).scalar()
                or 0
            )
            if duplicados_txn:
                _error(errores, f"dim_txn tiene {duplicados_txn} llaves compuestas duplicadas.")

            duplicados_saldos = int(
                conexion.execute(
                    text(
                        "SELECT COUNT(*) FROM ("
                        "SELECT num_cta, cve_fecha_informacion, COUNT(*) AS n "
                        "FROM fact_saldos_captacoloca_m "
                        "GROUP BY num_cta, cve_fecha_informacion HAVING COUNT(*) > 1"
                        ")"
                    )
                ).scalar()
                or 0
            )
            if duplicados_saldos:
                _error(errores, f"fact_saldos_captacoloca_m tiene {duplicados_saldos} llaves duplicadas.")

        # Advertencia útil: columnas de negocio no curadas. No es un error.
        columnas_sin_curar = [
            f"{tabla}.{columna['nombre']}"
            for tabla, meta in tablas_catalogo.items()
            for columna in meta.get("columnas", [])
            if columna.get("confianza") == "PROPUESTO"
        ]
        if columnas_sin_curar:
            advertencias.append(
                f"{len(columnas_sin_curar)} columnas usan metadatos genéricos "
                "(introspección) y aún no tienen descripción curada."
            )

        return {
            "ok": not errores,
            "errores": errores,
            "advertencias": advertencias,
            "resumen": {
                **catalogo.get("resumen", {}),
                "tablas_reales": len(tablas_reales),
                "vistas_reales": len(vistas_reales),
                "relaciones_validadas": sum(
                    1 for r in catalogo.get("relaciones", []) if r.get("validada")
                ),
                "metricas_runtime": metricas_runtime_total,
                "columnas_sin_curar": len(columnas_sin_curar),
            },
        }
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Glosario
# ---------------------------------------------------------------------------


def glosario(catalogo: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Devuelve términos buscables de tablas, columnas, métricas y glosario externo."""
    catalogo = catalogo or construir_catalogo()
    terminos: list[dict[str, Any]] = []
    vistos: set[tuple[str, str, str, str]] = set()

    def agregar(
        termino: str,
        tipo: str,
        descripcion: str,
        *,
        tabla: str = "",
        columna: str = "",
        confianza: str = "PROPUESTO",
    ) -> None:
        clave = (_normalizar(termino), tipo, tabla, columna)
        if not termino or clave in vistos:
            return
        vistos.add(clave)
        terminos.append(
            {
                "termino": termino,
                "tipo": tipo,
                "tabla": tabla,
                "columna": columna,
                "descripcion": descripcion,
                "confianza": confianza,
            }
        )

    for nombre, tabla in catalogo.get("tablas", {}).items():
        agregar(
            tabla.get("nombre_negocio", nombre),
            "TABLA",
            tabla.get("descripcion", ""),
            tabla=nombre,
            confianza=tabla.get("confianza", "PROPUESTO"),
        )
        for alias in tabla.get("alias", []):
            agregar(
                alias,
                "TABLA",
                tabla.get("descripcion", ""),
                tabla=nombre,
                confianza=tabla.get("confianza", "PROPUESTO"),
            )
        for columna in tabla.get("columnas", []):
            agregar(
                columna.get("nombre_negocio", columna["nombre"]),
                "COLUMNA",
                columna.get("descripcion", ""),
                tabla=nombre,
                columna=columna["nombre"],
                confianza=columna.get("confianza", "PROPUESTO"),
            )
            for sinonimo in columna.get("sinonimos", []):
                agregar(
                    sinonimo,
                    "COLUMNA",
                    columna.get("descripcion", ""),
                    tabla=nombre,
                    columna=columna["nombre"],
                    confianza=columna.get("confianza", "PROPUESTO"),
                )

    for metrica in catalogo.get("metricas", []):
        agregar(
            metrica.get("nombre_negocio", metrica.get("nombre", "")),
            "METRICA",
            metrica.get("descripcion", ""),
            tabla=metrica.get("vista", ""),
            columna=metrica.get("columna", ""),
            confianza=metrica.get("confianza", "PROPUESTO"),
        )
        agregar(
            metrica.get("nombre", ""),
            "METRICA",
            metrica.get("descripcion", ""),
            tabla=metrica.get("vista", ""),
            columna=metrica.get("columna", ""),
            confianza=metrica.get("confianza", "PROPUESTO"),
        )
        for sinonimo in metrica.get("sinonimos", []):
            agregar(
                sinonimo,
                "METRICA",
                metrica.get("descripcion", ""),
                tabla=metrica.get("vista", ""),
                columna=metrica.get("columna", ""),
                confianza=metrica.get("confianza", "PROPUESTO"),
            )

    for externo in catalogo.get("glosario_externo", []):
        agregar(
            externo.get("variable", ""),
            "GLOSARIO_EXTERNO",
            externo.get("descripcion", ""),
            confianza="EXTERNO",
        )

    return terminos


def buscar_glosario(
    termino: str,
    catalogo: dict[str, Any] | None = None,
    *,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """Busca por coincidencia normalizada en el glosario del catálogo."""
    consulta = _normalizar(termino).strip()
    if not consulta:
        return []
    resultados = []
    for item in glosario(catalogo):
        texto = _normalizar(item["termino"])
        if consulta in texto:
            resultados.append((0 if texto == consulta else 1, item))
    resultados.sort(key=lambda par: (par[0], len(par[1]["termino"])))
    return [item for _, item in resultados[:limite]]


__all__ = [
    "COLUMNAS_NEGOCIO",
    "GLOSARIO_EXTERNO",
    "METRICAS",
    "REGLAS",
    "RELACIONES",
    "TABLAS_NEGOCIO",
    "buscar_glosario",
    "construir_catalogo",
    "glosario",
    "validar_catalogo",
]
