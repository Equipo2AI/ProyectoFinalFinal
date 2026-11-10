"""Validación determinista de SQL para el Data Analyst.

La generación de SQL sigue siendo responsabilidad del hub/LLM. Este módulo no
genera consultas: revisa una consulta candidata contra el catálogo real, las
reglas de solo lectura y el alcance del perfil autenticado.

La validación usa el AST de ``sqlglot`` en vez de expresiones regulares. Eso
permite detectar tablas/columnas inexistentes, operaciones de escritura y
filtros de cliente sin depender de que el texto "se vea bien".
"""

from __future__ import annotations

from typing import Any

import sqlglot
from pydantic import BaseModel, Field
from sqlalchemy import Engine, inspect, text

try:  # sqlglot usa nombres de dialecto distintos a SQLAlchemy
    from sqlglot import exp
except ImportError:  # pragma: no cover - sqlglot es dependencia obligatoria
    exp = None  # type: ignore[assignment]


class ResultadoValidacionSQL(BaseModel):
    """Resultado estable para que el hub y la tool puedan iterar."""

    ok: bool
    sql: str
    normalizado: str | None = None
    errores: list[str] = Field(default_factory=list)
    tablas: list[str] = Field(default_factory=list)
    columnas: list[str] = Field(default_factory=list)
    pii: list[str] = Field(default_factory=list)
    requiere_limite: bool = False
    limite_aplicado: int | None = None


def _normalizar_perfil(perfil: dict[str, Any] | None) -> tuple[str, int | None]:
    perfil = perfil or {}
    rol = str(perfil.get("rol") or "").strip().lower()
    cliente_id = perfil.get("cliente_id")
    try:
        cliente_id = int(cliente_id) if cliente_id is not None else None
    except (TypeError, ValueError):
        cliente_id = None
    return rol, cliente_id


def _dialecto(engine: Engine) -> str:
    nombre = (engine.dialect.name or "sqlite").lower()
    if nombre in {"postgres", "postgresql"}:
        return "postgres"
    return "sqlite"


def _catalogo(engine: Engine) -> dict[str, set[str]]:
    inspector = inspect(engine)
    objetos = [*inspector.get_table_names(), *inspector.get_view_names()]
    return {
        objeto: {columna["name"] for columna in inspector.get_columns(objeto)}
        for objeto in objetos
    }


def _literal_entero(nodo: Any) -> int | None:
    if isinstance(nodo, exp.Literal) and nodo.is_int:
        try:
            return int(nodo.this)
        except (TypeError, ValueError):
            return None
    return None


def _columna_es(nodo: Any, nombre: str, alias: str | None = None) -> bool:
    if not isinstance(nodo, exp.Column):
        return False
    if nodo.name.casefold() != nombre.casefold():
        return False
    if alias is None:
        return True
    if alias == "":
        return not nodo.table
    return (nodo.table or "").casefold() == alias.casefold()


def _valores_filtro(
    nodo: Any, columna: str, alias: str | None = None
) -> list[int] | None:
    """Devuelve los valores de un filtro seguro, o ``None`` si no filtra.

    Un ``AND`` puede aportar el filtro por cualquiera de sus lados. Un ``OR``
    solo es seguro si ambos lados filtran; de lo contrario una rama podría
    devolver filas ajenas. Para el alcance del cliente, el filtro debe ser de
    igualdad o ``IN`` con literales enteros.
    """
    if nodo is None:
        return None
    if isinstance(nodo, exp.Paren):
        return _valores_filtro(nodo.this, columna, alias)
    if isinstance(nodo, exp.And):
        izquierda = _valores_filtro(nodo.left, columna, alias)
        derecha = _valores_filtro(nodo.right, columna, alias)
        if izquierda is None and derecha is None:
            return None
        return list(dict.fromkeys((izquierda or []) + (derecha or [])))
    if isinstance(nodo, exp.Or):
        izquierda = _valores_filtro(nodo.left, columna, alias)
        derecha = _valores_filtro(nodo.right, columna, alias)
        if izquierda is None or derecha is None:
            return None
        return list(dict.fromkeys(izquierda + derecha))
    if isinstance(nodo, exp.EQ):
        if _columna_es(nodo.left, columna, alias):
            valor = _literal_entero(nodo.right)
            return [valor] if valor is not None else None
        if _columna_es(nodo.right, columna, alias):
            valor = _literal_entero(nodo.left)
            return [valor] if valor is not None else None
    if isinstance(nodo, exp.In):
        if _columna_es(nodo.this, columna, alias):
            valores = [_literal_entero(valor) for valor in nodo.expressions]
            if valores and all(valor is not None for valor in valores):
                return [valor for valor in valores if valor is not None]
    return None


def _valores_en_consulta(
    expr: Any, columna: str, alias: str | None = None
) -> list[int] | None:
    """Busca filtros seguros en WHERE/HAVING de la consulta principal."""
    encontrados: list[int] = []
    for clave in ("where", "having"):
        clausula = expr.args.get(clave)
        if clausula is None:
            continue
        valores = _valores_filtro(clausula.this, columna, alias)
        if valores is not None:
            encontrados.extend(valores)
    if not encontrados:
        return None
    return list(dict.fromkeys(encontrados))


def _aliases_con_columna(
    tablas: list[Any], catalogo: dict[str, set[str]], columna: str
) -> set[str]:
    return {
        tabla.alias_or_name
        for tabla in tablas
        if columna in catalogo.get(tabla.name, set())
    }


def _valores_para_alias(
    select: Any,
    columna: str,
    alias: str,
    tablas: list[Any],
    catalogo: dict[str, set[str]],
) -> list[int] | None:
    """Valores de un filtro para una tabla concreta.

    Un filtro sin calificar solo se acepta si es inequívoco: una sola tabla
    del alcance tiene esa columna. Si hay varias, el SQL sería ambiguo y no se
    usa como prueba de RLS.
    """
    valores = _valores_en_consulta(select, columna, alias)
    if valores is not None:
        return valores
    if len(_aliases_con_columna(tablas, catalogo, columna)) == 1:
        return _valores_en_consulta(select, columna, "")
    return None


def _grafo_igualdad(select: Any) -> dict[str, set[str]]:
    """Aristas entre alias unidos por igualdad de ``num_cte`` o ``num_cta``."""
    grafo: dict[str, set[str]] = {}
    for join in select.args.get("joins") or []:
        on = join.args.get("on")
        if on is None:
            continue
        for igualdad in on.find_all(exp.EQ):
            izquierda, derecha = igualdad.left, igualdad.right
            if not isinstance(izquierda, exp.Column) or not isinstance(
                derecha, exp.Column
            ):
                continue
            if izquierda.name.casefold() != derecha.name.casefold():
                continue
            if izquierda.name.casefold() not in {"num_cte", "num_cta"}:
                continue
            alias_izq = (izquierda.table or "").casefold()
            alias_der = (derecha.table or "").casefold()
            if alias_izq and alias_der:
                grafo.setdefault(alias_izq, set()).add(alias_der)
                grafo.setdefault(alias_der, set()).add(alias_izq)
    return grafo


def _validar_rls(
    expr: Any,
    engine: Engine,
    catalogo: dict[str, set[str]],
    cliente_id: int,
) -> list[str]:
    """Verifica que cada tabla de cliente esté acotada al cliente autenticado.

    No basta con que exista un ``WHERE num_cte = ...`` en la consulta: un
    self-join, una subconsulta o un CTE podrían leer otros clientes. Se revisa
    cada ``SELECT`` y se exige que cada tabla con ``num_cte``/``num_cta`` esté
    filtrada o conectada por igualdad a una tabla ya filtrada.
    """
    errores: list[str] = []

    for select in expr.find_all(exp.Select):
        tablas = [
            tabla
            for tabla in select.find_all(exp.Table)
            if tabla.find_ancestor(exp.Select) is select and tabla.name in catalogo
        ]
        clientes = [
            tabla
            for tabla in tablas
            if "num_cte" in catalogo[tabla.name] or "num_cta" in catalogo[tabla.name]
        ]
        if not clientes:
            continue

        cubiertos: set[str] = set()
        for tabla in clientes:
            alias = tabla.alias_or_name
            valores_cte = _valores_para_alias(
                select, "num_cte", alias, tablas, catalogo
            )
            if valores_cte is not None:
                if set(valores_cte) != {cliente_id}:
                    errores.append(
                        "El filtro num_cte no corresponde al cliente autenticado."
                    )
                else:
                    cubiertos.add(alias)
                continue

            valores_cta = _valores_para_alias(
                select, "num_cta", alias, tablas, catalogo
            )
            if valores_cta is not None:
                if _cuentas_autorizadas(engine, cliente_id, valores_cta):
                    cubiertos.add(alias)
                else:
                    errores.append(
                        "La cuenta consultada no pertenece al cliente autenticado."
                    )

        if errores:
            return list(dict.fromkeys(errores))

        grafo = _grafo_igualdad(select)
        pendientes = list(cubiertos)
        while pendientes:
            actual = pendientes.pop()
            for vecino in grafo.get(actual, set()):
                if vecino not in cubiertos:
                    cubiertos.add(vecino)
                    pendientes.append(vecino)

        faltantes = [
            tabla.alias_or_name
            for tabla in clientes
            if tabla.alias_or_name not in cubiertos
        ]
        if faltantes:
            errores.append(
                "Cada tabla de clientes debe filtrar por num_cte del cliente "
                "autenticado, por una de sus cuentas (num_cta) o estar unida "
                "por igualdad a una tabla ya filtrada. Faltan: "
                + ", ".join(sorted(set(faltantes)))
                + "."
            )

    return list(dict.fromkeys(errores))


def _cuentas_autorizadas(engine: Engine, cliente_id: int, cuentas: list[int]) -> bool:
    """Comprueba que cada cuenta pertenece al cliente autenticado."""
    if not cuentas or len(cuentas) > 100:
        return False
    try:
        with engine.connect() as conexion:
            for cuenta in cuentas:
                fila = conexion.execute(
                    text("SELECT num_cte FROM dim_cuenta WHERE num_cta = :cuenta"),
                    {"cuenta": cuenta},
                ).first()
                if fila is None or int(fila[0]) != cliente_id:
                    return False
    except Exception:  # noqa: BLE001 - sin catálogo no se puede autorizar
        return False
    return True


def _pii_en_consulta(
    expr: Any,
    catalogo: dict[str, set[str]],
    columnas_sensibles: set[str],
) -> list[str]:
    sensibles = {columna.casefold() for columna in columnas_sensibles}
    pii: set[str] = set()

    for columna in expr.find_all(exp.Column):
        if columna.name.casefold() in sensibles:
            pii.add(columna.name)

    # SELECT * puede exponer PII sin nombrar la columna.
    tablas = {
        tabla.name for tabla in expr.find_all(exp.Table) if tabla.name in catalogo
    }
    tiene_star = any(
        isinstance(expresion, exp.Star)
        or (isinstance(expresion, exp.Column) and isinstance(expresion.this, exp.Star))
        for select in expr.find_all(exp.Select)
        for expresion in select.expressions
    )
    if tiene_star:
        for tabla in tablas:
            for columna in catalogo[tabla]:
                if columna.casefold() in sensibles:
                    pii.add(columna)

    return sorted(pii)


def _validar_columnas(
    expr: Any,
    catalogo: dict[str, set[str]],
    tablas: list[str],
) -> list[str]:
    ctes = {cte.alias_or_name for cte in expr.find_all(exp.CTE)}
    subconsultas = {sub.alias_or_name for sub in expr.find_all(exp.Subquery)}
    alias_tabla = {
        tabla.alias_or_name: tabla.name
        for tabla in expr.find_all(exp.Table)
        if tabla.name not in ctes
    }
    alias_salida = {alias.alias_or_name for alias in expr.find_all(exp.Alias)}
    tablas_reales = [tabla for tabla in tablas if tabla in catalogo]
    errores: list[str] = []

    for columna in expr.find_all(exp.Column):
        nombre = columna.name
        if not nombre or nombre == "*" or nombre in alias_salida:
            continue

        if columna.table:
            tabla = alias_tabla.get(columna.table, columna.table)
            if tabla in ctes or tabla in subconsultas:
                continue
            if tabla not in catalogo:
                continue
            if nombre not in catalogo[tabla]:
                errores.append(
                    f"La columna '{nombre}' no existe en la tabla '{tabla}'."
                )
            continue

        # Una consulta con CTE puede referirse a columnas calculadas que no
        # están en el catálogo base; se valida el alcance y las tablas, no el
        # nombre de cada columna derivada.
        if ctes:
            continue
        if tablas_reales and not any(
            nombre in catalogo[tabla] for tabla in tablas_reales
        ):
            errores.append(
                f"La columna '{nombre}' no existe en las tablas consultadas."
            )

    return errores


def validar_sql(
    sql: str,
    *,
    engine: Engine,
    perfil: dict[str, Any] | None = None,
    tablas_excluidas: set[str] | None = None,
    columnas_sensibles: set[str] | None = None,
    limite_max: int | None = None,
) -> ResultadoValidacionSQL:
    """Valida una consulta candidata sin ejecutarla.

    Reglas:
    - una sola sentencia;
    - solo ``SELECT``/``WITH ... SELECT``;
    - tablas y columnas existentes y permitidas;
    - sin columnas PII para roles no autorizados;
    - para ``rol=usuario``, filtro explícito por ``num_cte`` o por una
      ``num_cta`` que pertenezca al cliente;
    - si se indica ``limite_max`` y la consulta no trae ``LIMIT``, se agrega.
    """
    tablas_excluidas = set(tablas_excluidas or set())
    columnas_sensibles = {
        columna.casefold() for columna in (columnas_sensibles or set())
    }
    rol, cliente_id = _normalizar_perfil(perfil)
    permitir_pii = rol == "superusuario"
    excluidas = tablas_excluidas - ({"dim_cliente_persona"} if permitir_pii else set())

    resultado = ResultadoValidacionSQL(ok=False, sql=sql)
    consulta = (sql or "").strip()
    if not consulta:
        resultado.errores.append("La consulta está vacía.")
        return resultado

    dialecto = _dialecto(engine)
    try:
        expresiones = sqlglot.parse(consulta, read=dialecto)
    except Exception as error:  # noqa: BLE001 - se devuelve al hub para corregir
        resultado.errores.append(f"No se pudo interpretar el SQL: {error}")
        return resultado

    if len(expresiones) != 1:
        resultado.errores.append("Solo se permite una consulta por llamada.")
        return resultado

    expr = expresiones[0]
    if expr is None or not isinstance(expr, (exp.Select, exp.Union)):
        resultado.errores.append("Solo se permiten consultas SELECT o WITH ... SELECT.")
        return resultado

    nombres_prohibidos = (
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Pragma",
        "TruncateTable",
        "Merge",
        "Grant",
        "Revoke",
        "Attach",
        "Detach",
        "Vacuum",
        "Call",
        "Execute",
        "Copy",
        "Transaction",
        "Commit",
        "Rollback",
        "Into",
    )
    prohibidas = tuple(
        getattr(exp, nombre) for nombre in nombres_prohibidos if hasattr(exp, nombre)
    )
    for prohibida in prohibidas:
        if expr.find(prohibida) is not None:
            resultado.errores.append(
                "La consulta contiene una operación de escritura o administración."
            )
            return resultado

    try:
        catalogo = _catalogo(engine)
    except Exception as error:  # noqa: BLE001
        resultado.errores.append(
            f"No se pudo leer el catálogo de la base: {type(error).__name__}: {error}"
        )
        return resultado

    ctes = {cte.alias_or_name for cte in expr.find_all(exp.CTE)}
    tablas = sorted(
        {tabla.name for tabla in expr.find_all(exp.Table) if tabla.name not in ctes}
    )
    resultado.tablas = tablas

    for tabla in tablas:
        if tabla not in catalogo:
            resultado.errores.append(f"La tabla '{tabla}' no existe.")
        elif tabla in excluidas:
            resultado.errores.append(
                f"La tabla '{tabla}' no está disponible para análisis."
            )

    if resultado.errores:
        return resultado

    resultado.columnas = sorted(
        {
            columna.name
            for columna in expr.find_all(exp.Column)
            if columna.name and columna.name != "*"
        }
    )
    resultado.pii = _pii_en_consulta(expr, catalogo, columnas_sensibles)
    if resultado.pii and not permitir_pii:
        resultado.errores.append(
            "La consulta devuelve datos personales ("
            + ", ".join(resultado.pii)
            + "). Usa agregados o la tool de PII autorizada."
        )

    resultado.errores.extend(_validar_columnas(expr, catalogo, tablas))
    if resultado.errores:
        return resultado

    # Row-level security: solo se aplica cuando hay un perfil de usuario
    # autenticado. Sin perfil se conserva el modo demo de las pruebas actuales.
    if rol == "usuario" and cliente_id is not None:
        resultado.errores.extend(_validar_rls(expr, engine, catalogo, cliente_id))

    resultado.requiere_limite = any(
        select.args.get("limit") is None for select in expr.find_all(exp.Select)
    )
    if limite_max is not None and resultado.requiere_limite:
        expr = expr.limit(limite_max)
        resultado.limite_aplicado = limite_max
    resultado.normalizado = expr.sql(dialect=dialecto)
    resultado.ok = not resultado.errores
    return resultado


__all__ = ["ResultadoValidacionSQL", "validar_sql"]
