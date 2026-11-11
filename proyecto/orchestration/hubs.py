"""Hub de dominio reutilizable sobre LangChain.

El hub recibe una tarea y un contexto, decide qué tools llamar y devuelve un
``ResultadoTarea``. Este módulo no conoce el grafo ni el orquestador; solo
ejecuta una tarea con las tools que se le asignaron.
"""

from __future__ import annotations

import base64
import json
import re
import time
import unicodedata
from typing import Any

from langchain.agents import create_agent
from proyecto.config.ollama_runtime import ChatOllama
from pydantic import BaseModel, ConfigDict, Field

from proyecto.charts import generar_grafica
from proyecto.guardrails import limpiar_base64, redactar_pii, verificar_cifras
from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend, _respuesta_terminal, nombre_buscado
from proyecto.orchestration.tareas import (
    ContextoHub,
    DominioTarea,
    EstadoResultado,
    ResultadoTarea,
    Tarea,
)


class SalidaHub(BaseModel):
    """Respuesta estructurada que debe entregar un hub."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    respuesta: str = Field(min_length=1)
    datos: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    fuentes: list[str] = Field(default_factory=list)
    evidencia: dict[str, Any] = Field(default_factory=dict)
    requiere_aclaracion: bool = False


class HubLangChain:
    """Hub genérico con un loop de tools y salida estructurada.

    Cada especialista configura su propio prompt, modelo y conjunto de tools.
    El hub decide cuántas veces llamarlas; el límite se controla con
    ``max_iterations``.
    """

    def __init__(
        self,
        *,
        nombre: str,
        dominio: DominioTarea,
        tools: list[Any],
        system_prompt: str,
        modelo: str,
        max_iterations: int = 20,
        temperatura: float = 0,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations debe ser mayor que cero.")
        self.nombre = nombre
        self.dominio = dominio
        self.tools = list(tools)
        self.system_prompt = system_prompt
        self.modelo = modelo
        self.max_iterations = max_iterations
        self.temperatura = temperatura
        self._agente = None

    def _get_agente(self):
        if self._agente is None:
            self._agente = create_agent(
                model=ChatOllama(
                    model=self.modelo,
                    temperature=self.temperatura,
                ),
                tools=self.tools,
                system_prompt=self.system_prompt,
                response_format=SalidaHub,
            )
        return self._agente

    def construir_entrada(
        self,
        tarea: Tarea,
        contexto: ContextoHub,
    ) -> str:
        """Formatea la instrucción del orquestador y el contexto curado.

        No incluye el historial crudo ni el prompt original del usuario.
        """
        partes = [
            "CONTEXTO DE SESIÓN:",
            f"- session_id: {contexto.session_id}",
            f"- perfil: {contexto.perfil or '(vacío)'}",
            f"- slots: {contexto.slots or '(vacíos)'}",
            f"- ultima_sql: {contexto.ultima_sql or '(ninguna)'}",
            f"- ultimo_resultado: {contexto.ultimo_resultado or '(ninguno)'}",
            f"- ultimo_agente: {contexto.ultimo_agente or '(ninguno)'}",
            f"- ultima_intencion: {contexto.ultima_intencion or '(ninguna)'}",
            "",
            "INSTRUCCIÓN DEL ORQUESTADOR:",
            tarea.instruccion,
            "",
            "TAREA:",
            f"- id: {tarea.id}",
            f"- dominio: {tarea.dominio.value}",
            f"- objetivo: {tarea.objetivo}",
            f"- resultado_esperado: {tarea.resultado_esperado}",
        ]
        if tarea.contexto:
            partes.extend(["", "CONTEXTO DE LA TAREA:", tarea.contexto])
        if tarea.subobjetivos:
            partes.append("- subobjetivos:")
            partes.extend(f"  - {subobjetivo}" for subobjetivo in tarea.subobjetivos)
        if tarea.restricciones:
            partes.append("- restricciones:")
            partes.extend(f"  - {restriccion}" for restriccion in tarea.restricciones)
        if tarea.criterios:
            partes.append("- criterios:")
            partes.extend(f"  - {criterio}" for criterio in tarea.criterios)
        if tarea.evidencia_requerida:
            partes.append("- evidencia_requerida:")
            partes.extend(f"  - {evidencia}" for evidencia in tarea.evidencia_requerida)

        partes.extend(
            [
                "",
                "Ejecuta la tarea con las herramientas disponibles. No inventes "
                "datos, fuentes ni columnas. Si falta un dato indispensable, "
                "devuelve requiere_aclaracion=true y explica qué necesitas.",
            ]
        )
        return "\n".join(partes)

    def ejecutar(
        self,
        tarea: Tarea,
        contexto: ContextoHub,
    ) -> ResultadoTarea:
        """Ejecuta la tarea y normaliza la respuesta al contrato común."""
        if tarea.dominio != self.dominio:
            return ResultadoTarea(
                tarea_id=tarea.id,
                estado=EstadoResultado.ERROR,
                error=(
                    f"El hub {self.nombre!r} no puede ejecutar tareas del dominio "
                    f"{tarea.dominio.value!r}."
                ),
            )

        inicio = time.perf_counter()
        try:
            from contextlib import closing
            from langchain_core.messages import ToolMessage
            with closing(self._get_agente().stream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.construir_entrada(tarea, contexto),
                        }
                    ]
                },
                config={"recursion_limit": self.max_iterations},
                stream_mode="values",
            )) as estados:
                for resultado in estados:
                    terminales = []
                    for mensaje in reversed(resultado.get("messages") or []):
                        if not isinstance(mensaje, ToolMessage):
                            break
                        terminal = _respuesta_terminal(mensaje.name, mensaje.content)
                        if terminal:
                            terminales.append(terminal)
                    if terminales:
                        terminal = next((t for t in terminales if t.startswith("Acceso denegado:")), terminales[0])
                        resultado = {**resultado, "structured_response": SalidaHub(respuesta=terminal, evidencia={"terminal": True})}
                        break
        except Exception as error:  # noqa: BLE001 - se informa al orquestador
            return ResultadoTarea(
                tarea_id=tarea.id,
                estado=EstadoResultado.ERROR,
                error=f"{type(error).__name__}: {error}",
                duracion=round(time.perf_counter() - inicio, 2),
            )

        duracion = round(time.perf_counter() - inicio, 2)
        mensajes = list(resultado.get("messages") or [])
        salida = resultado.get("structured_response")
        if salida is None:
            # Algunos modelos de Ollama no llenan structured_response aunque
            # create_agent haya ejecutado las tools. Se recupera la última
            # respuesta del modelo y se valida contra SalidaHub.
            salida = _salida_desde_mensajes(mensajes)
        if salida is None:
            return ResultadoTarea(
                tarea_id=tarea.id,
                estado=EstadoResultado.ERROR,
                error="El hub no devolvió una respuesta estructurada.",
                trazas=_trazas_de_mensajes(mensajes),
                tool_calls=_contar_tool_calls(mensajes),
                duracion=duracion,
            )
        if not isinstance(salida, SalidaHub):
            try:
                salida = SalidaHub.model_validate(salida)
            except Exception as error:  # noqa: BLE001 - contrato inválido
                return ResultadoTarea(
                    tarea_id=tarea.id,
                    estado=EstadoResultado.ERROR,
                    error=f"Salida del hub fuera de contrato: {error}",
                    trazas=_trazas_de_mensajes(mensajes),
                    tool_calls=_contar_tool_calls(mensajes),
                    duracion=duracion,
                )

        estado = (
            EstadoResultado.REQUIERE_ACLARACION
            if salida.requiere_aclaracion
            else EstadoResultado.OK
        )
        return ResultadoTarea(
            tarea_id=tarea.id,
            estado=estado,
            respuesta=salida.respuesta,
            datos=salida.datos,
            sql=salida.sql,
            fuentes=salida.fuentes,
            trazas=_trazas_de_mensajes(mensajes),
            tool_calls=_contar_tool_calls(mensajes),
            duracion=duracion,
            evidencia=salida.evidencia,
        )


PROMPT_DATA_ANALYST = """
Eres el hub de Data Analyst de un banco. Tu tarea es responder una pregunta
cuantitativa usando la base SQL de solo lectura.

Trabaja con estas herramientas de bajo nivel:
- listar_tablas y describir_tabla para conocer el catálogo;
- valores_columna para conocer categorías antes de filtrar;
- validar_sql para revisar una consulta candidata;
- ejecutar_consulta para obtener hasta 50 filas de una sola consulta SELECT;
- catalogo_metricas y consultar_metrica para métricas certificadas;
- resolver_periodo para fechas y resolver_valor para categorías ambiguas;
- graficar_consulta para generar una gráfica agregada cuando el usuario la pida.

Reglas:
- Si la pregunta coincide con una métrica certificada, prefiere
  consultar_metrica antes que SQL libre. Usa las dimensiones del catálogo.
  Si no hay métrica aplicable, compón el SQL tú mismo.
- Si el usuario pide una gráfica, después de ejecutar_consulta llama a
  graficar_consulta con el mismo SQL. Usa tipo="auto" si no especificó uno.
  No grafiques identificadores ni datos personales.
- Valida el SQL antes de ejecutarlo. Si la validación falla, corrige y vuelve a
  intentar; no ejecutes una consulta que devolvió error.
- Para un usuario normal, toda consulta sobre datos de clientes debe filtrar
  por su num_cte o por una cuenta que le pertenezca. No intentes ampliar el
  alcance ni consultar a otros clientes.
- No inventes cifras, columnas, tablas ni fuentes. Los números de la respuesta
  deben salir de ejecutar_consulta.
- Si la pregunta es ambigua o falta un dato indispensable, devuelve
  requiere_aclaracion=true y explica qué necesitas.
- Devuelve una respuesta breve en español, el SQL ejecutado, los datos
  tabulares y las fuentes usadas.
"""


class HubDataAnalyst(HubLangChain):
    """Hub de Data Analyst sobre las tools SQL de bajo nivel.

    No usa ``analizar_datos`` ni un segundo agente dentro de una tool. El perfil
    de sesión se fija antes de invocar las tools y el resultado se contrasta con
    la última consulta realmente ejecutada.
    """

    def __init__(self, backend: DataAnalystSQLBackend | None = None) -> None:
        self.backend = backend or DataAnalystSQLBackend()
        super().__init__(
            nombre="hub_data_analyst",
            dominio=DominioTarea.DATA_ANALYST,
            tools=list(self.backend._tools),
            system_prompt=PROMPT_DATA_ANALYST,
            modelo=self.backend.modelo,
            max_iterations=30,
        )

    def ejecutar(self, tarea: Tarea, contexto: ContextoHub) -> ResultadoTarea:
        from proyecto.security.local_auth import authorize_request
        try:
            authorize_request(tarea.instruccion)
        except PermissionError as error:
            return ResultadoTarea(tarea_id=tarea.id, estado=EstadoResultado.OK,
                                  respuesta=str(error), evidencia={"terminal": True})
        if nombre_buscado(tarea.instruccion):
            return ResultadoTarea(tarea_id=tarea.id, estado=EstadoResultado.OK,
                                  respuesta=self.backend.procesar(tarea.instruccion),
                                  evidencia={"terminal": True})
        self.backend.configurar_perfil(contexto.perfil)
        resultado = super().ejecutar(tarea, contexto)
        if resultado.evidencia.get("terminal"):
            return resultado
        resultado = self._contrastar_con_sql_ejecutado(resultado)
        return self._agregar_grafica_si_aplica(resultado, tarea)

    @staticmethod
    def _pide_grafica(texto: str) -> bool:
        normalizado = unicodedata.normalize("NFKD", texto or "")
        normalizado = "".join(
            letra for letra in normalizado if not unicodedata.combining(letra)
        ).casefold()
        return bool(
            re.search(
                r"\b(grafic\w*|chart\w*|plot\w*|visualiz\w*|diagram\w*|"
                r"barras|pastel|tendencia)\b",
                normalizado,
            )
        )

    @staticmethod
    def _normalizar_sql(texto: str) -> str:
        return " ".join(str(texto or "").split()).rstrip(";").casefold()

    @classmethod
    def _grafica_desde_trazas(
        cls,
        resultado: ResultadoTarea,
    ) -> tuple[bytes | None, dict[str, Any]]:
        for traza in reversed(resultado.trazas):
            if traza.get("name") not in {
                "graficar_consulta",
                "mcp_graficar_consulta",
            }:
                continue
            args = traza.get("args") or {}
            sql_traza = cls._normalizar_sql(args.get("sql") or "")
            sql_resultado = cls._normalizar_sql(resultado.sql or "")
            if sql_resultado and sql_traza and sql_traza != sql_resultado:
                continue
            try:
                salida = json.loads(traza.get("salida") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(salida, dict) or salida.get("error"):
                continue
            try:
                png = base64.b64decode(salida.get("png_base64") or "")
            except Exception:  # noqa: BLE001 - salida externa mal formada
                continue
            if png:
                return png, dict(salida.get("especificacion") or {})
        return None, {}

    @staticmethod
    def _grafica_automatica(
        resultado: ResultadoTarea,
    ) -> tuple[bytes | None, dict[str, Any], str | None]:
        datos = resultado.datos or {}
        if datos.get("truncado"):
            return None, {}, "El resultado está truncado; no se generó la gráfica."
        columnas = datos.get("columnas")
        filas = datos.get("filas")
        if not isinstance(columnas, list) or not isinstance(filas, list):
            return None, {}, "No hay datos tabulares para graficar."
        try:
            grafica = generar_grafica(columnas, filas, tipo="auto")
        except Exception as error:  # noqa: BLE001 - la gráfica es opcional
            return None, {}, f"{type(error).__name__}: {error}"
        return (
            grafica["png"],
            dict(grafica["especificacion"]),
            "; ".join(grafica.get("advertencias") or []) or None,
        )

    def _agregar_grafica_si_aplica(
        self, resultado: ResultadoTarea, tarea: Tarea
    ) -> ResultadoTarea:
        if resultado.estado is not EstadoResultado.OK:
            return resultado
        if resultado.grafica is not None:
            return resultado
        if not self._pide_grafica(tarea.instruccion):
            return resultado

        png, especificacion = self._grafica_desde_trazas(resultado)
        error = None
        if png is None:
            png, especificacion, error = self._grafica_automatica(resultado)
        if png is None:
            evidencia = dict(resultado.evidencia)
            if error:
                evidencia["grafica_error"] = error
            return resultado.model_copy(update={"evidencia": evidencia})

        evidencia = dict(resultado.evidencia)
        evidencia["grafica"] = especificacion
        return resultado.model_copy(
            update={
                "grafica": png,
                "grafica_especificacion": especificacion,
                "evidencia": evidencia,
            }
        )

    @staticmethod
    def _contrastar_con_sql_ejecutado(resultado: ResultadoTarea) -> ResultadoTarea:
        """Sustituye SQL/datos declarados por los de la última ejecución real."""
        if resultado.estado not in {
            EstadoResultado.OK,
            EstadoResultado.REQUIERE_ACLARACION,
        }:
            return resultado

        ejecuciones = [
            traza
            for traza in resultado.trazas
            if traza.get("name") in {"ejecutar_consulta", "consultar_metrica"}
        ]
        if not ejecuciones:
            return resultado

        ultima = ejecuciones[-1]
        sql = str((ultima.get("args") or {}).get("sql") or "").strip() or None
        try:
            datos = json.loads(ultima.get("salida") or "{}")
        except (TypeError, ValueError):
            datos = {"salida_cruda": ultima.get("salida")}
        if not isinstance(datos, dict):
            datos = {"resultado": datos}

        # consultar_metrica devuelve el SQL que construyó el backend.
        sql = str(datos.get("sql") or sql or "").strip() or None

        if "error" in datos:
            return resultado.model_copy(
                update={
                    "estado": EstadoResultado.ERROR,
                    "error": str(datos.get("error") or "La consulta SQL falló."),
                    "sql": sql,
                    "datos": datos,
                }
            )

        texto, pii = redactar_pii(limpiar_base64(resultado.respuesta))
        no_verificadas = verificar_cifras(
            texto, {"sql": sql, "resultado": datos}
        )
        evidencia = dict(resultado.evidencia)
        evidencia.update(
            {
                "sql": sql,
                "columnas": datos.get("columnas") or [],
                "filas": len(datos.get("filas") or []),
                "truncado": bool(datos.get("truncado")),
                "cifras_no_verificadas": no_verificadas,
            }
        )
        if pii:
            evidencia["pii_redactada"] = pii
        return resultado.model_copy(
            update={
                "respuesta": texto,
                "sql": sql,
                "datos": datos,
                "fuentes": resultado.fuentes or ["banamex.db"],
                "evidencia": evidencia,
            }
        )


def _salida_desde_mensajes(mensajes: list[Any]) -> SalidaHub | None:
    """Fallback para modelos que no devuelven structured_response."""
    for mensaje in reversed(mensajes):
        tipo = str(getattr(mensaje, "type", ""))
        if tipo not in {"ai", "assistant"}:
            continue
        texto = _texto(getattr(mensaje, "content", "")).strip()
        if not texto:
            continue

        # Algunos modelos envuelven el JSON en un bloque Markdown.
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.lower().startswith("json"):
                texto = texto[4:].lstrip()
        try:
            datos = json.loads(texto)
        except (TypeError, ValueError):
            datos = None
        if isinstance(datos, dict):
            try:
                return SalidaHub.model_validate(datos)
            except Exception:  # noqa: BLE001 - se usa el texto plano
                pass
        return SalidaHub(respuesta=texto)

    return None


def _contar_tool_calls(mensajes: list[Any]) -> int:
    total = 0
    for mensaje in mensajes:
        total += len(getattr(mensaje, "tool_calls", None) or [])
    return total


def _trazas_de_mensajes(mensajes: list[Any]) -> list[dict[str, Any]]:
    """Empareja tool calls con sus resultados para dejar una traza auditable."""
    registros: list[dict[str, Any]] = []
    por_id: dict[str, dict[str, Any]] = {}

    for mensaje in mensajes:
        for llamada in getattr(mensaje, "tool_calls", None) or []:
            registro = {
                "name": llamada.get("name"),
                "args": llamada.get("args") or {},
                "salida": "",
                "estado": "ok",
            }
            registros.append(registro)
            if llamada.get("id"):
                por_id[llamada["id"]] = registro

        if getattr(mensaje, "type", "") == "tool":
            registro = por_id.get(getattr(mensaje, "tool_call_id", None))
            if registro is None and registros:
                registro = registros[-1]
            if registro is None:
                continue
            salida = _texto(getattr(mensaje, "content", ""))
            registro["salida"] = salida
            if getattr(mensaje, "status", None) == "error":
                registro["estado"] = "error"

    return registros


def _texto(contenido: Any) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "".join(
            bloque.get("text", "")
            for bloque in contenido
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )
    return str(contenido or "")


__all__ = ["HubDataAnalyst", "HubLangChain", "SalidaHub"]
