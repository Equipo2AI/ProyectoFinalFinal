"""Adapta la redacción de la respuesta final al tipo de usuario.

Hay dos piezas:

1. ``detectar_perfil``: determinista (reglas, sin LLM). El rol del login da el
   estilo base y el texto de la consulta lo ajusta.
2. ``redactar_respuesta``: una llamada al modelo del orquestador que reescribe
   la respuesta ya verificada con el estilo elegido. Si la reescritura agrega
   una cifra que no está en la evidencia, se descarta y se devuelve la original.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable

from proyecto.orchestration.comun import _normalizar

SIMPLE = "simple"
TECNICO = "tecnico"

# Rol de local_auth -> estilo base.
_ESTILO_POR_ROL = {"normal": SIMPLE, "auditor": TECNICO}

# Solo estos roles pueden ver el SQL ejecutado, y únicamente si lo piden.
# Un usuario normal no tiene permiso de metadatos (governance) y el SQL
# revela nombres de tablas y columnas.
_ROLES_CON_SQL = {"auditor"}

_PIDE_SIMPLE = re.compile(
    r"\b(explica\w*\s+(?:\w+\s+)?(?:simple|sencill\w*|facil\w*)|"
    r"en palabras simples|sin tecnicismos|resumen corto|no entiendo)\b"
)
_PIDE_TECNICO = re.compile(
    r"\b(justifica\w*|metodologia|a detalle|detallad\w*|con fuentes|"
    r"que tablas?|columnas?|esquema|supuestos|limitaciones|"
    r"articulo\s+\d+|fundamento\w*)\b"
)
_PIDE_SQL = re.compile(r"\b(sql|query)\b")

_RE_BLOQUE_SQL = re.compile(r"```sql\s*.*?```", flags=re.IGNORECASE | re.DOTALL)
# Encabezados sueltos del SQL: "SQL ejecutado:", "SQL ejecutado (solo lectura):".
_RE_LINEA_SQL = re.compile(r"(?im)^\s*\**\s*(?:sql|consulta sql)\b[^\n]*:\s*\**\s*$")
# Separador de tabla Markdown: |---|:---:|
_RE_SEPARADOR_TABLA = re.compile(r"^\|?[\s:|-]+\|?$")
# Elementos de lista: "- x", "* x", "1. x"
_RE_ITEM_LISTA = re.compile(r"^(?:[-*•]|\d+[.)])\s+\S")
# Números con separador de miles y decimales: 1,234.56 / 12.5 / 300
_RE_NUMERO = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Marcadores de listas numeradas ("1. ", "2) ") que el modelo puede agregar.
_RE_MARCADOR_LISTA = re.compile(r"(?m)^\s*\d+[.)]\s")

# Herramientas cuya salida debe llegar íntegra (OCR / conversión a Markdown).
HERRAMIENTAS_LITERALES = {
    "mcp_extraer_texto_imagen",
    "mcp_convertir_documento_markdown",
}

# Evidencia estructurada o normativa: se conserva literal si es la única
# fuente. Si hay varias fuentes o el usuario pide interpretar/combinar, el
# orquestador sí puede reescribirla, con las salvaguardas de cifras y fuentes.
HERRAMIENTAS_EVIDENCIA = {
    "mcp_buscar_normatividad",
    "mcp_inspeccionar_dataset",
    "mcp_validar_calidad_datos",
    "mcp_evaluar_calidad",
}

_PIDE_INTERPRETACION = re.compile(
    r"\b(interpret\w*|combin\w*|compar\w*|relacion\w*|integra\w*|"
    r"sintetiz\w*|resum\w*|explic\w*|entiend\w*|signific\w*|"
    r"trata\w*|dice|dicen)\b"
)


@dataclass(frozen=True)
class PerfilRespuesta:
    estilo: str          # SIMPLE o TECNICO
    mostrar_sql: bool    # el usuario lo pidió y su rol lo permite
    origen: str          # "rol" o "consulta": qué decidió el estilo

    def a_dict(self) -> dict:
        return asdict(self)


def detectar_perfil(rol_login: str, consulta: str) -> PerfilRespuesta:
    """Combina el rol del login con lo que pide la consulta."""
    texto = _normalizar(consulta or "")
    pide_sql = bool(_PIDE_SQL.search(texto))
    mostrar_sql = pide_sql and rol_login in _ROLES_CON_SQL

    # 1. Pedir algo simple gana aunque el usuario sea auditor.
    if _PIDE_SIMPLE.search(texto):
        return PerfilRespuesta(SIMPLE, mostrar_sql, "consulta")
    # 2. Pedir justificación o SQL sube a técnico aunque sea usuario normal.
    if _PIDE_TECNICO.search(texto) or pide_sql:
        return PerfilRespuesta(TECNICO, mostrar_sql, "consulta")
    # 3. Si no pide nada especial, manda el rol. Rol desconocido -> simple.
    return PerfilRespuesta(_ESTILO_POR_ROL.get(rol_login, SIMPLE), False, "rol")


def perfil_desde_dict(datos: dict | None) -> PerfilRespuesta | None:
    if not datos or datos.get("estilo") not in {SIMPLE, TECNICO}:
        return None
    return PerfilRespuesta(
        datos["estilo"], bool(datos.get("mostrar_sql")), str(datos.get("origen") or "rol")
    )


# --------------------------------------------------------------------------- #
# Instrucciones de estilo (se reutilizan en la síntesis y en la redacción)
# --------------------------------------------------------------------------- #

_INSTRUCCIONES = {
    SIMPLE: """
Estilo de respuesta: USUARIO GENERAL (cliente sin conocimientos técnicos).
- Usa lenguaje cotidiano, cálido y directo. Entre 2 y 5 frases, o una lista corta.
- Empieza por la respuesta concreta a lo que preguntó.
- No muestres SQL, nombres de tablas, columnas, vistas ni términos técnicos.
- Si hay una fuente normativa, menciona solo el nombre del documento.
- Copia las cifras exactamente como vienen; puedes explicar qué significan.
""",
    TECNICO: """
Estilo de respuesta: USUARIO TÉCNICO (auditor / analista).
Organiza la respuesta en estas secciones con encabezados en negritas:
**Respuesta**: el resultado concreto.
**Fuentes y tablas**: tablas o vistas consultadas y referencias normativas
(archivo y página) tal como aparecen en la evidencia.
**Metodología**: qué especialistas intervinieron y cómo se llegó al resultado.
**Limitaciones y supuestos**: filtros aplicados, calidad de datos, datos
faltantes o alcance de la evidencia. Si la evidencia no menciona ninguna, dilo.
- Copia las cifras exactamente como vienen.
""",
}

_SIN_SQL = "- No incluyas el SQL ejecutado ni bloques de código SQL.\n"
_CON_SQL = "- Incluye el SQL ejecutado en un bloque ```sql al final, sin modificarlo.\n"


def instrucciones_estilo(perfil: PerfilRespuesta | None) -> str:
    """Texto para añadir a un system prompt existente."""
    if perfil is None:
        return ""
    return _INSTRUCCIONES[perfil.estilo] + (_CON_SQL if perfil.mostrar_sql else _SIN_SQL)


_PROMPT_REDACCION = """
Eres el redactor final de un asistente bancario. Recibes una respuesta ya
verificada por especialistas y la evidencia que la respalda. Reescríbela para
el tipo de usuario indicado.

Reglas estrictas:
- No cambies, redondees ni inventes cifras, fechas, artículos ni fuentes.
- No agregues información que no esté en la respuesta o en la evidencia.
- Puedes interpretar, resumir o combinar la evidencia, pero conserva
  literalmente citas, documentos, páginas, tablas, nombres de columnas y
  conteos. Si no puedes combinarlos sin perder ese detalle, deja ambos bloques.
- Si la respuesta informa un error o falta de datos, consérvalo.
- No incluyas rutas locales ni imágenes Markdown.
- Responde en el idioma de la consulta y entrega solo la respuesta final.
"""


# --------------------------------------------------------------------------- #
# Utilidades deterministas
# --------------------------------------------------------------------------- #

def quitar_sql(texto: str) -> str:
    """Elimina bloques ```sql``` y sus encabezados sueltos."""
    sin_bloques = _RE_BLOQUE_SQL.sub("", texto or "")
    sin_encabezados = _RE_LINEA_SQL.sub("", sin_bloques)
    return re.sub(r"\n{3,}", "\n\n", sin_encabezados).strip()


def _numeros(texto: str) -> set[str]:
    limpio = _RE_MARCADOR_LISTA.sub(" ", texto or "")
    return {n.replace(",", "").rstrip(".") for n in _RE_NUMERO.findall(limpio)}


def _conteos(texto: str) -> set[str]:
    """Conteos que se deducen sin inventar: filas de tablas y elementos de listas.

    Si la evidencia trae una tabla de 4 cuentas, "tienes 4 cuentas" es válido
    aunque el número 4 no aparezca escrito.
    """
    conteos: set[str] = set()
    filas = items = 0
    for linea in [*(texto or "").splitlines(), ""]:
        limpia = linea.strip()
        if limpia.startswith("|"):
            if not _RE_SEPARADOR_TABLA.fullmatch(limpia):
                filas += 1
            continue
        if filas > 1:
            conteos.add(str(filas - 1))  # se descuenta la fila de encabezado
        filas = 0
        if _RE_ITEM_LISTA.match(limpia):
            items += 1
            continue
        if items:
            conteos.add(str(items))
        items = 0
    return conteos


def cifras_conservadas(evidencia: str, redactada: str) -> bool:
    """True si toda cifra de la versión redactada aparece en la evidencia
    o es un conteo de filas/elementos de la evidencia."""
    return _numeros(redactada) <= _numeros(evidencia) | _conteos(evidencia)


def debe_redactar(
    respuesta: str,
    trazas: list[dict],
    resultados: dict,
    consulta: str = "",
) -> bool:
    """Decide si vale la pena la llamada extra al modelo.

    OCR y Markdown siempre son literales. La evidencia normativa o del Data
    Steward también se conserva si es la única fuente; se reescribe cuando hay
    varias fuentes que combinar o cuando el usuario pide interpretar/resumir.
    """
    if not (respuesta or "").strip():
        return False
    nombres = {str(t.get("name")) for t in trazas or []}
    if nombres & HERRAMIENTAS_LITERALES:
        return False  # OCR o Markdown: el texto debe llegar completo.
    if resultados and all(
        (r or {}).get("estado") == "error" for r in resultados.values()
    ):
        return False  # Solo hay errores; no hay nada que reformular.
    if nombres & HERRAMIENTAS_EVIDENCIA:
        validos = [
            r
            for r in (resultados or {}).values()
            if (r or {}).get("estado") != "error"
        ]
        if len(validos) > 1:
            return True  # Hay varias fuentes: el modelo puede combinarlas.
        return bool(_PIDE_INTERPRETACION.search(_normalizar(consulta or "")))
    return True


def _evidencia(resultados: dict) -> str:
    bloques: list[str] = []
    for agente, resultado in (resultados or {}).items():
        resultado = resultado or {}
        if resultado.get("estado") == "error":
            continue
        partes = [f"[{agente}]", str(resultado.get("respuesta") or "")[:6000]]
        if resultado.get("sql"):
            partes.append(f"SQL ejecutado:\n{str(resultado['sql'])[:2000]}")
        if resultado.get("fuentes"):
            partes.append("Fuentes: " + ", ".join(map(str, resultado["fuentes"])))
        bloques.append("\n".join(partes))
    return "\n\n---\n\n".join(bloques)


def _llamar_ollama(modelo: str, mensajes: list[dict]) -> str:
    from proyecto.config.ollama_runtime import ChatOllama

    respuesta = ChatOllama(
        model=modelo, temperature=0, reasoning=False, timeout=60
    ).invoke(mensajes)
    contenido = getattr(respuesta, "content", respuesta)
    if isinstance(contenido, list):
        contenido = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in contenido
        )
    return str(contenido or "").strip()


def redactar_respuesta(
    consulta: str,
    respuesta: str,
    perfil: PerfilRespuesta,
    resultados: dict,
    modelo: str,
    llamar_modelo: Callable[[str, list[dict]], str] = _llamar_ollama,
) -> str:
    """Reescribe ``respuesta`` con el estilo del perfil, sin alterar cifras.

    ``llamar_modelo`` se inyecta en los tests para no depender de Ollama.
    """
    respaldo = respuesta if perfil.mostrar_sql else quitar_sql(respuesta)
    if set(resultados) == {'data_steward'} or (
        set(resultados) == {'data_analyst'} and re.search(r'\b(?:tablas?|vistas?)\b', consulta, re.I)
    ):
        return respaldo

    evidencia = _evidencia(resultados)
    contenido = (
        f"Consulta del usuario:\n{consulta}\n\n"
        f"Respuesta verificada:\n{respuesta}\n\n"
        f"Evidencia de los especialistas:\n{evidencia or '(la misma respuesta)'}"
    )
    try:
        redactada = llamar_modelo(
            modelo,
            [
                {"role": "system", "content": _PROMPT_REDACCION + instrucciones_estilo(perfil)},
                {"role": "user", "content": contenido},
            ],
        )
    except Exception:  # noqa: BLE001 - si falla, se entrega la respuesta verificada
        return respaldo

    if not redactada or not cifras_conservadas(f"{respuesta}\n{evidencia}", redactada):
        return respaldo
    return redactada if perfil.mostrar_sql else quitar_sql(redactada)
