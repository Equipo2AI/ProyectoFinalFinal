"""Gráficas deterministas para el Data Analyst.

La tool no ejecuta código generado por el modelo ni acepta rutas arbitrarias.
Recibe columnas y filas ya validadas por el backend SQL y devuelve un PNG en
memoria. El objetivo es que la imagen sea una representación fiel del resultado
de la consulta, no una fuente de datos independiente.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from typing import Any

MAX_FILAS_GRAFICA = 50
MAX_CATEGORIAS = 20
MAX_CATEGORIAS_PIE = 10
MAX_TITULO = 120

TIPOS_GRAFICA = {"auto", "bar", "line", "scatter", "pie"}
ORDENES = {"ninguno", "asc", "desc"}

# Además de PII, se evita graficar identificadores individuales.
COLUMNAS_PROHIBIDAS = {
    "num_cte",
    "num_cta",
    "cliente_id",
    "id_cliente",
    "curp",
    "rfc",
    "correo",
    "email",
    "telefono",
    "celular",
    "domicilio",
    "direccion",
    "nombre",
    "nombres",
    "apellido",
    "apellidos",
    "fec_nac",
    "fecha_nacimiento",
}

_NUMERO = re.compile(r"^-?\d+(?:\.\d+)?$")
_FECHA = re.compile(r"^\d{4}(?:-\d{2}){0,2}$")


def _numero(valor: Any) -> float | None:
    """Convierte valores numéricos reales o cadenas numéricas simples."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float, Decimal)):
        return float(valor)
    if isinstance(valor, str) and _NUMERO.match(valor.strip()):
        return float(valor.strip())
    return None


def _parece_fecha(valor: Any) -> bool:
    return isinstance(valor, str) and bool(_FECHA.match(valor.strip()))


def _parece_anio_mes(valor: Any) -> bool:
    numero = _numero(valor)
    if numero is None or not numero.is_integer():
        return False
    entero = int(numero)
    return 190001 <= entero <= 210012 and 1 <= entero % 100 <= 12


def _texto_categoria(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _titulo_limpio(titulo: str) -> str:
    titulo = " ".join(str(titulo or "").split())
    return titulo[:MAX_TITULO]


def _columnas_unicas(columnas: list[Any], filas: list[Any]) -> list[str]:
    nombres = [str(columna).strip() for columna in columnas]
    if not nombres or any(not nombre for nombre in nombres):
        raise ValueError("La gráfica necesita columnas con nombre.")
    if len(set(nombres)) != len(nombres):
        raise ValueError("Las columnas de la gráfica deben tener nombres únicos.")
    if not filas:
        raise ValueError("No hay filas para graficar.")
    if len(filas) > MAX_FILAS_GRAFICA:
        raise ValueError(
            f"La gráfica admite como máximo {MAX_FILAS_GRAFICA} filas; "
            "agrega una agrupación o un LIMIT."
        )
    for fila in filas:
        if not isinstance(fila, (list, tuple)) or len(fila) != len(nombres):
            raise ValueError("Cada fila debe tener el mismo número de columnas.")
    return nombres


def _elegir_ejes(
    columnas: list[str], filas: list[list[Any]], eje_x: str, eje_y: str
) -> tuple[str, str, list[str]]:
    advertencias: list[str] = []
    permitidas = [columna for columna in columnas if columna not in COLUMNAS_PROHIBIDAS]
    if not permitidas:
        raise ValueError("Las columnas disponibles no se pueden graficar.")

    if eje_x:
        if eje_x not in columnas:
            raise ValueError(f"El eje X '{eje_x}' no existe en el resultado.")
        if eje_x in COLUMNAS_PROHIBIDAS:
            raise ValueError(f"El eje X '{eje_x}' es un identificador y no se grafica.")
    else:
        indice_x = next(
            (
                indice
                for indice, columna in enumerate(columnas)
                if columna in permitidas
                and any(_numero(fila[indice]) is None for fila in filas)
            ),
            None,
        )
        if indice_x is None:
            indice_x = columnas.index(permitidas[0])
        eje_x = columnas[indice_x]
        advertencias.append(f"Eje X elegido automáticamente: {eje_x}.")

    if eje_y:
        if eje_y not in columnas:
            raise ValueError(f"El eje Y '{eje_y}' no existe en el resultado.")
        if eje_y in COLUMNAS_PROHIBIDAS:
            raise ValueError(f"El eje Y '{eje_y}' es un identificador y no se grafica.")
        indice_y = columnas.index(eje_y)
        if all(_numero(fila[indice_y]) is None for fila in filas):
            raise ValueError(
                "El eje Y debe ser una columna numérica. "
                "Agrega un COUNT, SUM, AVG u otra métrica."
            )
    else:
        indice_y = next(
            (
                indice
                for indice, columna in enumerate(columnas)
                if columna in permitidas
                and columna != eje_x
                and all(_numero(fila[indice]) is not None for fila in filas)
            ),
            None,
        )
        if indice_y is None:
            raise ValueError(
                "No se encontró una columna numérica para el eje Y. "
                "Agrega un COUNT, SUM, AVG u otra métrica."
            )
        eje_y = columnas[indice_y]
        advertencias.append(f"Eje Y elegido automáticamente: {eje_y}.")

    if eje_x == eje_y:
        raise ValueError("Los ejes X e Y deben ser columnas distintas.")
    return eje_x, eje_y, advertencias


def _elegir_tipo(
    tipo: str, x_valores: list[Any], filas: list[list[Any]]
) -> str:
    tipo = (tipo or "auto").strip().lower()
    if tipo not in TIPOS_GRAFICA:
        raise ValueError(
            f"Tipo de gráfica no soportado: {tipo}. "
            f"Usa uno de: {', '.join(sorted(TIPOS_GRAFICA))}."
        )
    if tipo != "auto":
        return tipo
    if len(x_valores) > 2 and all(_parece_fecha(valor) for valor in x_valores):
        return "line"
    if len(x_valores) > 2 and all(_parece_anio_mes(valor) for valor in x_valores):
        return "line"
    if x_valores and all(_numero(valor) is not None for valor in x_valores):
        return "scatter"
    # Barras es el valor seguro por defecto: una gráfica de pastel solo se usa
    # cuando el usuario lo pide explícitamente.
    return "bar"


def _preparar_puntos(
    columnas: list[str],
    filas: list[list[Any]],
    eje_x: str,
    eje_y: str,
    tipo: str,
    orden: str,
) -> tuple[list[Any], list[float], list[str]]:
    if orden not in ORDENES:
        raise ValueError("El orden debe ser 'ninguno', 'asc' o 'desc'.")

    indice_x = columnas.index(eje_x)
    indice_y = columnas.index(eje_y)
    puntos: list[tuple[Any, float]] = []
    advertencias: list[str] = []
    descartadas = 0

    for fila in filas:
        y = _numero(fila[indice_y])
        x = (
            _numero(fila[indice_x])
            if tipo == "scatter"
            else _texto_categoria(fila[indice_x])
        )
        if y is None or x in (None, ""):
            descartadas += 1
            continue
        puntos.append((x, y))

    if descartadas:
        advertencias.append(
            f"Se omitieron {descartadas} filas con X vacío o Y no numérico."
        )
    if not puntos:
        raise ValueError("No quedaron filas válidas para graficar.")

    if tipo in {"bar", "line", "pie"}:
        x_limpios = [punto[0] for punto in puntos]
        if len(set(x_limpios)) != len(x_limpios):
            raise ValueError(
                "Hay valores repetidos en el eje X. Agrupa la consulta para "
                "que cada categoría tenga una sola fila."
            )

    if tipo == "pie" and len(puntos) > MAX_CATEGORIAS_PIE:
        raise ValueError(
            f"La gráfica de pastel admite máximo {MAX_CATEGORIAS_PIE} categorías."
        )
    if tipo in {"bar", "pie"} and len(puntos) > MAX_CATEGORIAS:
        raise ValueError(
            f"La gráfica admite máximo {MAX_CATEGORIAS} categorías; "
            "agrega un TOP/LIMIT o una agrupación."
        )

    if orden != "ninguno":
        if tipo in {"line", "scatter"}:
            def llave(punto):
                x = punto[0]
                return x if isinstance(x, (int, float)) else str(x)
        else:
            def llave(punto):
                return punto[1]
        puntos.sort(key=llave, reverse=orden == "desc")

    x_valores = [punto[0] for punto in puntos]
    y_valores = [punto[1] for punto in puntos]
    return x_valores, y_valores, advertencias


def _dibujar(
    tipo: str,
    x_valores: list[Any],
    y_valores: list[float],
    eje_x: str,
    eje_y: str,
    titulo: str,
) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figura = Figure(figsize=(8.4, 4.8), dpi=150, facecolor="white")
    canvas = FigureCanvasAgg(figura)
    ejes = figura.add_subplot(111)

    if tipo == "bar":
        ejes.bar(x_valores, y_valores, color="#C8102E")
        if len(x_valores) > 8 or any(len(str(valor)) > 12 for valor in x_valores):
            ejes.tick_params(axis="x", rotation=35, labelsize=8)
        ejes.grid(axis="y", alpha=0.25)
    elif tipo == "line":
        ejes.plot(
            x_valores,
            y_valores,
            marker="o",
            linewidth=2,
            color="#003B5C",
        )
        ejes.grid(alpha=0.25)
    elif tipo == "scatter":
        ejes.scatter(x_valores, y_valores, color="#C8102E", alpha=0.75)
        ejes.grid(alpha=0.25)
    elif tipo == "pie":
        ejes.pie(
            y_valores,
            labels=x_valores,
            autopct="%1.1f%%",
            startangle=90,
            textprops={"fontsize": 8},
        )
        ejes.axis("equal")
    else:  # pragma: no cover - _elegir_tipo ya valida
        raise ValueError(f"Tipo de gráfica no soportado: {tipo}")

    ejes.set_title(titulo or f"{eje_y} por {eje_x}", fontsize=12, pad=12)
    if tipo != "pie":
        ejes.set_xlabel(eje_x, fontsize=9)
        ejes.set_ylabel(eje_y, fontsize=9)
        ejes.tick_params(axis="both", labelsize=8)
    figura.tight_layout()

    buffer = io.BytesIO()
    canvas.print_png(buffer)
    return buffer.getvalue()


def generar_grafica(
    columnas: list[Any],
    filas: list[Any],
    *,
    tipo: str = "auto",
    eje_x: str = "",
    eje_y: str = "",
    titulo: str = "",
    orden: str = "ninguno",
) -> dict[str, Any]:
    """Genera un PNG determinista y devuelve bytes + especificación.

    No interpreta SQL ni consulta la base. El llamador debe pasar el resultado
    real de una consulta validada.
    """
    nombres = _columnas_unicas(columnas, filas)
    eje_x, eje_y, advertencias = _elegir_ejes(nombres, filas, eje_x, eje_y)
    indice_x = nombres.index(eje_x)
    x_originales = [fila[indice_x] for fila in filas]
    tipo_elegido = _elegir_tipo(tipo, x_originales, filas)
    x_valores, y_valores, mas_advertencias = _preparar_puntos(
        nombres, filas, eje_x, eje_y, tipo_elegido, orden
    )
    advertencias.extend(mas_advertencias)
    titulo = _titulo_limpio(titulo)
    png = _dibujar(
        tipo_elegido,
        x_valores,
        y_valores,
        eje_x,
        eje_y,
        titulo,
    )
    return {
        "png": png,
        "especificacion": {
            "tipo": tipo_elegido,
            "eje_x": eje_x,
            "eje_y": eje_y,
            "titulo": titulo or f"{eje_y} por {eje_x}",
            "orden": orden,
            "filas": len(x_valores),
            "categorias": len(
                {str(valor) for valor in x_valores}
                if tipo_elegido != "scatter"
                else x_valores
            ),
        },
        "advertencias": advertencias,
    }


__all__ = [
    "COLUMNAS_PROHIBIDAS",
    "MAX_CATEGORIAS",
    "MAX_FILAS_GRAFICA",
    "TIPOS_GRAFICA",
    "generar_grafica",
]
