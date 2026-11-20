"""Catálogo semántico adaptado a la base sintética del MVP.

Este paquete no reemplaza la capa semántica de diseño que vive en
``capa_semantica_financiera_v1/``. La traduce a un catálogo ejecutable contra
la base SQLite real y permite validar que tablas, columnas, métricas, reglas y
relaciones sigan coincidiendo con ella.

La integración con los agentes es deliberadamente aditiva: por ahora el
catálogo se puede consultar y validar sin cambiar las respuestas existentes.
"""

from proyecto.semantic.catalogo import (
    buscar_glosario,
    construir_catalogo,
    glosario,
    validar_catalogo,
)

__all__ = [
    "buscar_glosario",
    "construir_catalogo",
    "glosario",
    "validar_catalogo",
]
