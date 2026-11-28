"""Planificador por capacidades del orquestador.

El planner propone pasos; el grafo los valida y los ejecuta. El LLM no recibe
la capacidad de improvisar tools ni de saltarse permisos: solo puede elegir
capacidades registradas y expresar dependencias entre ellas.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

from proyecto.agents.document_intelligence_agent import (
    PATRON_RUTA_DOCUMENTO,
    PATRON_RUTA_IMAGEN,
)
from proyecto.config.models import modelo_de
from proyecto.config.ollama_runtime import ChatOllama
from proyecto.orchestration.capacidades import (
    CAPACIDADES,
    CAPACIDAD_POR_AGENTE,
    PasoPlan,
    PlanInvalido,
    capacidad,
    pasos_para_agentes,
    resumen_para_prompt,
    validar_pasos,
)


@dataclass(frozen=True)
class PlanCapacidades:
    pasos: list[PasoPlan]
    subpreguntas: list[str]
    razon: str
    origen: str


_PROMPT = """
Eres el planificador de un sistema multiagente bancario. Debes pensar qué
necesita la consulta y proponer un plan de pasos con las capacidades
registradas. No respondas la consulta y no inventes capacidades.

Capacidades disponibles:
{catalogo}

Reglas:
- Usa solo capacidades de la lista.
- Las pistas léxicas son sugerencias, no una decisión. Si la intención completa
  requiere otra capacidad, inclúyela.
- Puedes combinar varias capacidades y expresar dependencias con "depende_de".
- Usa "documentos.leer" solo si la consulta incluye una ruta local o hay una
  imagen pendiente. El documento activo del usuario no es una ruta.
- Si la consulta es un saludo, una aclaración o no requiere especialistas,
  devuelve una lista de pasos vacía.
- No inventes datos, fuentes, fechas ni requisitos. Si falta información,
  planifica solo lo que sí se puede hacer.
- Las fuentes deben venir de la salida de las capacidades; no las redactes tú.
- Conserva el idioma de la consulta para las subpreguntas.

Devuelve SOLO JSON válido:
{{
  "pasos": [
    {{
      "id": "requisitos",
      "capacidad": "normativa.buscar",
      "args": {{"query": "requisitos para cuenta de nómina"}},
      "depende_de": [],
      "proposito": "recuperar los requisitos y sus fuentes"
    }}
  ],
  "subpreguntas": ["subpregunta concreta"],
  "razon": "explicación breve"
}}

Si no hace falta ningún especialista, devuelve:
{{"pasos": [], "subpreguntas": [], "razon": "respuesta directa"}}
"""


def _texto(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            bloque.get("text", "")
            for bloque in content
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )
    return str(content or "")


def _leer_json(texto: str) -> Any:
    limpio = (texto or "").strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```(?:json)?\s*", "", limpio, flags=re.IGNORECASE)
        limpio = re.sub(r"\s*```$", "", limpio)
    try:
        return json.loads(limpio)
    except (TypeError, ValueError):
        coincidencia = re.search(r"\{.*\}", limpio, flags=re.DOTALL)
        if not coincidencia:
            return None
        try:
            return json.loads(coincidencia.group(0))
        except (TypeError, ValueError):
            return None


def _resumen_contexto(contexto: str) -> str:
    """Solo encabezados de documentos, nunca el cuerpo privado."""
    lineas = [
        linea.strip()
        for linea in str(contexto or "").splitlines()
        if linea.strip().startswith(
            ("Documento activo:", "Otros documentos disponibles")
        )
    ]
    return "\n".join(lineas)[:1200]


def _hay_ruta(consulta: str, imagen_pendiente: str | None) -> bool:
    return bool(
        imagen_pendiente
        or PATRON_RUTA_IMAGEN.search(consulta or "")
        or PATRON_RUTA_DOCUMENTO.search(consulta or "")
    )


def _paso_disponible(
    paso: PasoPlan,
    *,
    consulta: str,
    imagen_pendiente: str | None,
) -> bool:
    cap = capacidad(paso.capacidad)
    if cap.agente == "document_intelligence":
        return _hay_ruta(consulta, imagen_pendiente)
    return True


def _pasos_desde_json(
    datos: Any,
    *,
    consulta: str,
    imagen_pendiente: str | None,
) -> tuple[list[PasoPlan], list[str], str] | None:
    if not isinstance(datos, dict):
        return None

    brutos = datos.get("pasos")
    if brutos is None:
        # Compatibilidad con planes antiguos: ["data_analyst", ...].
        plan = datos.get("plan")
        if not isinstance(plan, list):
            return None
        pasos: list[PasoPlan] = []
        for item in plan:
            nombre = str(item or "").strip()
            if nombre == "directo":
                continue
            cap_nombre = (
                nombre
                if nombre in CAPACIDADES
                else CAPACIDAD_POR_AGENTE.get(nombre)
            )
            if not cap_nombre:
                return None
            pasos.append(
                PasoPlan(
                    id=f"paso_{len(pasos) + 1}",
                    capacidad=cap_nombre,
                )
            )
    else:
        if not isinstance(brutos, list):
            return None
        try:
            pasos = [PasoPlan.desde_dict(item) for item in brutos]
        except PlanInvalido:
            return None

    try:
        pasos = validar_pasos(pasos)
    except PlanInvalido:
        return None

    pasos = [
        paso
        for paso in pasos
        if _paso_disponible(
            paso,
            consulta=consulta,
            imagen_pendiente=imagen_pendiente,
        )
    ]
    # Si el plan dependía de un paso descartado, no se puede ejecutar a medias.
    ids = {paso.id for paso in pasos}
    if any(dependencia not in ids for paso in pasos for dependencia in paso.depende_de):
        return None

    subpreguntas_brutas = datos.get("subpreguntas")
    if not isinstance(subpreguntas_brutas, list):
        subpreguntas_brutas = []
    subpreguntas = [
        str(valor).strip()
        for valor in subpreguntas_brutas
        if str(valor).strip()
    ][:8]
    razon = str(datos.get("razon") or "").strip()[:500]
    return pasos, subpreguntas, razon


def planear_con_llm(
    consulta: str,
    *,
    pistas: dict[str, Any] | None = None,
    contexto_documentos: str = "",
    ultimo_agente: str | None = None,
    imagen_pendiente: str | None = None,
    modelo: str | None = None,
) -> PlanCapacidades | None:
    """Pide un plan al LLM y lo valida; None si no hay plan utilizable."""
    mensajes = [
        {
            "role": "system",
            "content": _PROMPT.format(catalogo=resumen_para_prompt()),
        },
        {
            "role": "user",
            "content": (
                "Consulta:\n"
                f"{consulta}\n\n"
                "Pistas léxicas (sugerencias, no obligaciones):\n"
                f"{json.dumps(pistas or {}, ensure_ascii=False)}\n\n"
                "Contexto documental disponible (solo encabezados):\n"
                f"{_resumen_contexto(contexto_documentos) or '(sin documentos)'}\n\n"
                f"Último agente: {ultimo_agente or '(ninguno)'}\n"
                f"Imagen pendiente: {imagen_pendiente or '(ninguna)'}"
            ),
        },
    ]
    try:
        respuesta = ChatOllama(
            model=modelo or modelo_de("orchestrator"),
            temperature=0,
            format="json",
            reasoning=False,
            timeout=30,
        ).invoke(mensajes)
        contenido = _texto(getattr(respuesta, "content", respuesta))
        datos = _leer_json(contenido)
        plan = _pasos_desde_json(
            datos,
            consulta=consulta,
            imagen_pendiente=imagen_pendiente,
        )
        if plan is None:
            return None
        pasos, subpreguntas, razon = plan
        return PlanCapacidades(
            pasos=pasos,
            subpreguntas=subpreguntas,
            razon=razon,
            origen="llm",
        )
    except Exception:  # noqa: BLE001 - el fallback determinista es deliberado
        return None


def plan_determinista(rutas: Iterable[str]) -> PlanCapacidades:
    pasos = pasos_para_agentes(rutas)
    return PlanCapacidades(
        pasos=pasos,
        subpreguntas=[],
        razon="plan determinista de respaldo",
        origen="reglas",
    )


def _debe_consultar_llm(
    modo: str,
    rutas: Iterable[str],
    consulta: str,
) -> bool:
    if modo == "rules":
        return False
    if modo in {"llm", "capabilities", "capacidades"}:
        return True

    # híbrido: el LLM piensa en toda consulta sustantiva, incluidas las que
    # las reglas ya mapean a un especialista; así puede descubrir una
    # combinación nueva. Solo se evita la llamada en saludos/aclaraciones muy
    # cortas que las reglas mandan a respuesta directa. ``rules`` sigue
    # disponible para operar sin LLM.
    lista = list(rutas)
    if lista == ["directo"] and len(consulta.strip()) <= 12:
        return False
    return True


def planear_pasos_hibrido(
    consulta: str,
    *,
    pistas: dict[str, Any],
    rutas_fallback: Iterable[str],
    contexto_documentos: str = "",
    ultimo_agente: str | None = None,
    imagen_pendiente: str | None = None,
    modelo: str | None = None,
) -> PlanCapacidades:
    """Planifica con LLM cuando aporta valor y cae a reglas si falla."""
    rutas_fallback = list(rutas_fallback)
    modo = os.getenv("ORCHESTRATION_PLANNER", "hybrid").strip().lower()
    if modo not in {"rules", "llm", "hybrid", "capabilities", "capacidades"}:
        modo = "hybrid"

    if not _debe_consultar_llm(modo, rutas_fallback, consulta):
        return plan_determinista(rutas_fallback)

    plan = planear_con_llm(
        consulta,
        pistas=pistas,
        contexto_documentos=contexto_documentos,
        ultimo_agente=ultimo_agente,
        imagen_pendiente=imagen_pendiente,
        modelo=modelo,
    )
    if plan is not None:
        # Si el LLM no propone nada pero las reglas sí encontraron un
        # especialista, no se pierde la ruta conocida. En consultas mixtas se
        # conservan además las capacidades deterministas que el LLM omitió.
        if not plan.pasos and rutas_fallback != ["directo"]:
            return plan_determinista(rutas_fallback)

        if plan.pasos and len(rutas_fallback) > 1:
            existentes = {paso.capacidad for paso in plan.pasos}
            extra = [
                paso
                for paso in pasos_para_agentes(rutas_fallback)
                if paso.capacidad not in existentes
                and _paso_disponible(
                    paso,
                    consulta=consulta,
                    imagen_pendiente=imagen_pendiente,
                )
            ]
            if extra:
                usados = {paso.id for paso in plan.pasos}
                ajustados: list[PasoPlan] = []
                for paso in extra:
                    nuevo_id = paso.id
                    sufijo = 2
                    while nuevo_id in usados:
                        nuevo_id = f"{paso.id}_{sufijo}"
                        sufijo += 1
                    usados.add(nuevo_id)
                    ajustados.append(
                        PasoPlan(
                            id=nuevo_id,
                            capacidad=paso.capacidad,
                            args=paso.args,
                            depende_de=paso.depende_de,
                            proposito=paso.proposito,
                        )
                    )
                plan = PlanCapacidades(
                    pasos=[*plan.pasos, *ajustados],
                    subpreguntas=plan.subpreguntas,
                    razon=(
                        f"{plan.razon} · se conservaron capacidades "
                        "deterministas omitidas"
                    ).strip(" ·"),
                    origen=plan.origen,
                )
        return plan
    return plan_determinista(rutas_fallback)
