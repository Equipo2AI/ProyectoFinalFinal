# Arquitectura de tareas y hubs de dominio

Este documento fija el contrato entre el orquestador y los subagentes. La idea
central es separar dos responsabilidades:

- **Orquestador:** entiende la consulta, construye tareas verificables, ordena
  dependencias y sintetiza resultados.
- **Hub de dominio:** recibe una tarea, decide qué herramientas usar y cuántas
  veces, y devuelve un resultado estructurado.

El orquestador **no** decide la secuencia de tools. El hub **no** decide el plan
global ni la respuesta final de varios dominios.

```text
Usuario
  |
  v
Orquestador
  |  plan de tareas
  v
Hub de dominio  --->  tools deterministas
  |  resultado estructurado
  v
Verificación -> Síntesis -> Respuesta
```

## Principios

1. **Una tarea, un resultado verificable.** Si dos resultados pueden fallar de
   forma independiente, son dos tareas.
2. **La tarea describe el qué, no el cómo.** No incluye nombres de tools, SQL ni
   prompts de especialista.
3. **El hub no recibe el prompt literal del usuario.** Recibe la
   `instruccion` que redacta el orquestador y un `ContextoHub` curado. El
   historial crudo y el prompt original se quedan en el orquestador.
4. **Las tools son deterministas.** Devuelven JSON validado; la autonomía vive
   en el hub, no en la tool.
5. **Todo resultado deja traza.** Tool calls, fuentes, SQL, error y duración.
6. **Los hubs tienen mínimo privilegio.** Cada uno solo ve las tools de su
   dominio.

## Tarea

Contrato implementado en `proyecto/orchestration/tareas.py`.

| Campo | Uso |
|---|---|
| `id` | Identificador único dentro del plan. |
| `dominio` | `data_steward`, `data_analyst`, `normatividad_rag`, `document_intelligence` o `compliance`. |
| `instruccion` | Lo que el orquestador le pide al hub. No es el prompt literal del usuario. |
| `objetivo` | Frase corta y normalizada de lo que se quiere lograr. |
| `resultado_esperado` | Qué debe devolver el hub para considerarse completo. |
| `subobjetivos` | Objetivos secundarios que el mismo hub puede cubrir en una corrida. |
| `contexto` | Contexto de sesión relevante para el hub. |
| `restricciones` | Límites de seguridad, alcance o formato. |
| `criterios` | Condiciones de aceptación verificables. |
| `evidencia_requerida` | Evidencia que debe acompañar el resultado. |
| `depende_de` | IDs de tareas que deben terminar antes. |
| `prioridad` | Desempate cuando varias tareas están listas. |
| `metadatos` | Datos auxiliares del planificador. |

Una tarea **no** debe incluir:

- `tool` o `herramienta`;
- argumentos de tools;
- SQL;
- el prompt del especialista;
- la respuesta final.

## ResultadoTarea

| Campo | Uso |
|---|---|
| `tarea_id` | Tarea que produjo el resultado. |
| `estado` | `ok`, `error`, `incompleto` o `requiere_aclaracion`. |
| `respuesta` | Respuesta del hub en lenguaje natural. |
| `datos` | Payload estructurado del dominio. |
| `sql` | SQL ejecutado, cuando aplique. |
| `fuentes` | Archivos, páginas o filas citadas. |
| `grafica` | PNG determinista, cuando el resultado lo permite. |
| `grafica_especificacion` | Tipo, ejes, título y orden usados para dibujar. |
| `trazas` | Tool calls y sus resultados. |
| `tool_calls` | Número de herramientas invocadas. |
| `duracion` | Segundos que tomó la tarea. |
| `error` | Mensaje cuando el estado no es `ok`. |
| `evidencia` | Evidencia adicional validable. |

## ContextoSesion y ContextoHub

El orquestador es dueño del estado completo de la conversación. Ese estado
incluye `historial_reciente`, `documentos`, `metadatos` y los slots.

Al hub **no** se le pasa `ContextoSesion` completo. El orquestador proyecta un
`ContextoHub` con solo lo que la tarea necesita:

```text
session_id
perfil: {rol, cliente_id, ...}
slots: {cliente_id, periodo, metrica, tabla, filtros}
ultima_sql
ultimo_resultado
ultimo_agente
ultima_intencion
```

El historial crudo y los documentos se quedan en el orquestador. Si un hub
necesita un fragmento documental, el orquestador lo incluye en el campo
`contexto` de la tarea, no en el historial.

Esto reemplaza el uso de prosa recortada como memoria operativa. Los
seguimientos se resuelven actualizando `slots`, no releyendo todo el chat.

## HubDominio

Interfaz esperada:

```python
class HubDominio(Protocol):
    nombre: str

    def ejecutar(
        self,
        tarea: Tarea,
        contexto: ContextoHub,
    ) -> ResultadoTarea: ...
```

El hub puede:

- llamar una tool varias veces;
- consultar metadatos antes de escribir SQL;
- validar y corregir SQL;
- hacer varias recuperaciones RAG;
- pedir una aclaración si falta un dato indispensable.

El hub no puede:

- salirse de su dominio;
- usar tools que no le fueron asignadas;
- inventar cifras, fuentes o columnas;
- modificar datos;
- exceder el máximo de iteraciones o el timeout.

## Ejemplos

### 1. Saldo por periodo

```python
Tarea(
    id="t-saldo-200",
    dominio="data_analyst",
    instruccion="Calcula el saldo total del cliente 200 para febrero-marzo.",
    objetivo="Saldo del cliente 200 para febrero-marzo",
    resultado_esperado="Monto total y desglose mensual, con el SQL ejecutado",
    contexto="slots: cliente_id=200, periodo=2026-02..2026-03, metrica=saldo",
    restricciones=["solo datos del cliente autenticado", "solo agregados"],
    criterios=["incluye cifras", "incluye SQL", "no muestra datos de terceros"],
    evidencia_requerida=["sql", "columnas", "filas"],
)
```

### 2. Seguimiento con nuevo cliente

```python
Tarea(
    id="t-saldo-300",
    dominio="data_analyst",
    instruccion=(
        "Calcula el saldo del cliente 300 para febrero-marzo, conservando la "
        "métrica y el periodo de la tarea anterior."
    ),
    objetivo="Saldo del cliente 300 para febrero-marzo",
    resultado_esperado="Monto total y desglose mensual, con el SQL ejecutado",
    contexto=(
        "slots heredados: periodo=2026-02..2026-03, metrica=saldo; "
        "cliente_id actualizado a 300"
    ),
    restricciones=["solo datos del cliente autenticado", "solo agregados"],
    criterios=["incluye cifras", "incluye SQL", "no muestra datos de terceros"],
    evidencia_requerida=["sql", "columnas", "filas"],
)
```

### 3. Varias consultas RAG

```python
Tarea(
    id="t-normativa-consentimiento",
    dominio="normatividad_rag",
    instruccion=(
        "Recupera evidencia normativa sobre consentimiento y transferencia de "
        "datos a terceros; cita cada fuente."
    ),
    objetivo="Evidencia normativa sobre consentimiento y transferencias",
    resultado_esperado=(
        "Respuesta con citas textuales y fuente/página para cada subobjetivo"
    ),
    subobjetivos=[
        "consentimiento para tratamiento de datos",
        "transferencia de datos a terceros",
    ],
    criterios=["cada subobjetivo tiene evidencia", "cada cita tiene fuente"],
    evidencia_requerida=["fragmentos", "fuentes"],
)
```

### 4. Calidad de una tabla

```python
Tarea(
    id="t-calidad-raw-clientes",
    dominio="data_steward",
    instruccion=(
        "Revisa la calidad de raw_clientes y reporta faltantes, duplicados y "
        "claves repetidas."
    ),
    objetivo="Reportar la calidad de raw_clientes",
    resultado_esperado=(
        "Conteos de faltantes, duplicados y claves repetidas, sin registros "
        "individuales"
    ),
    restricciones=["no mostrar PII", "solo agregados"],
    criterios=["incluye conteos", "no incluye registros personales"],
    evidencia_requerida=["json_metadatos", "conteos"],
)
```

### 5. OCR de una imagen

```python
Tarea(
    id="t-ocr-factura",
    dominio="document_intelligence",
    instruccion="Transcribe el texto visible de tests/modelo-factura-ex.png.",
    objetivo="Transcribir el texto visible de la imagen",
    resultado_esperado="Texto OCR literal de la imagen",
    restricciones=["no interpretar ni validar contra la base"],
    criterios=["incluye el texto transcrito", "incluye la ruta procesada"],
    evidencia_requerida=["texto", "ruta"],
)
```

## Flujo de ejecución

1. El planificador recibe la consulta y el `ContextoSesion`.
2. Produce un `PlanTareas` con IDs únicos y dependencias válidas.
3. El despachador ejecuta las tareas listas; las independientes pueden correr en
   paralelo.
4. Antes de ejecutar, el orquestador proyecta `ContextoSesion.para_hub()` y
   envía al hub solo la instrucción y el contexto curado.
5. Cada hub devuelve su `ResultadoTarea`.
6. El verificador revisa estado, criterios y evidencia. Si falla, reintenta la
   tarea con el error como contexto.
7. El sintetizador combina los resultados. Las cifras y citas se validan contra
   la evidencia estructurada.
8. Se actualiza `ContextoSesion` con `slots`, `ultima_sql`,
   `ultimo_resultado` y `ultimo_agente`.

## Gráficas deterministas

La gráfica es una tool, no código que el modelo escribe. `graficar_consulta`
recibe una consulta agregada, la valida con el mismo guard SQL y dibuja el PNG
con matplotlib en memoria.

Reglas:

- el usuario debe pedir una gráfica o visualización;
- máximo 50 filas y 20 categorías (10 para pastel);
- el eje Y debe ser numérico y el X no puede ser un identificador ni PII;
- no se grafican resultados truncados ni categorías repetidas;
- si el usuario no especifica ejes, se eligen de forma determinista;
- `auto` usa línea para fechas, dispersión para dos ejes numéricos y barras para
  categorías; pastel solo se usa si se pide explícitamente.

El PNG viaja como `ResultadoTarea.grafica`, el orquestador lo conserva en el
estado del turno y `servicio.py` lo expone como `Respuesta.grafica`. La UI ya lo
muestra y lo ofrece para descarga.

## Catálogo de métricas y resolución

El catálogo `proyecto/metrics.py` declara métricas cerradas sobre vistas
existentes. Cada entrada define agregación, columna, unidad y dimensiones
permitidas; no acepta tabla, columna ni SQL libre.

- `catalogo_metricas` publica la definición de cada métrica.
- `consultar_metrica` construye el SQL desde el catálogo, aplica el filtro de
  sesión y reutiliza el mismo guard SQL y control de PII.
- `resolver_periodo` convierte expresiones cerradas (`2026-02`, `febrero 2026`,
  `1T 2026`, `mes pasado`, `últimos 30 días`) a fechas y claves `YYYYMMDD` /
  `YYYYMM`.
- `resolver_valor` busca el valor canónico de una dimensión con los mismos
  controles de alcance que `valores_columna`.

`verificar_cifras` es la última barrera textual: compara los números de la
respuesta contra la evidencia y redacta correo, teléfono, CURP y RFC evidentes.
No sustituye la autorización ni el guard SQL.

## Memoria

Para esta etapa:

- la memoria de largo plazo queda desactivada;
- el estado de sesión es la fuente de verdad;
- el historial se limita por tokens, no por número de mensajes;
- no hay `_ORQUESTADOR` global compartido entre sesiones;
- cuando exista auth, `ContextoSesion` se llena desde la sesión autenticada.

## Autenticación

No se implementa todavía. El contrato ya reserva `perfil` dentro de
`ContextoSesion` y `ContextoHub`, y `perfil` viaja a las tools que lo necesiten.
La autorización real se aplicará después en la capa de tools, no en el prompt.

## Estado de implementación

Ya implementado:

- `proyecto/orchestration/tareas.py`: `Tarea`, `ResultadoTarea`,
  `ContextoSesion`, `ContextoHub`, `PlanTareas` y `HubDominio`.
- `proyecto/orchestration/hubs.py`: `HubLangChain` y `HubDataAnalyst`, con salida
  estructurada `SalidaHub`, tools SQL de bajo nivel y contraste del SQL/datos
  declarados contra la última ejecución real.
- `proyecto/charts.py` y `graficar_consulta`: PNG determinista con validación
  de tipos, columnas, PII, truncamiento y límites de categorías.
- `proyecto/metrics.py` y `proyecto/periods.py`: catálogo de métricas cerradas
  y resolución determinista de periodos; tools `catalogo_metricas`,
  `consultar_metrica`, `resolver_periodo` y `resolver_valor`.
- `proyecto/guardrails.py`: redacción de PII evidente y verificación de cifras
  contra la evidencia; el hub la aplica antes de devolver el resultado.
- `proyecto/mcp_servers/sql_guard.py`: validación determinista de SQL con
  `sqlglot`, catálogo real, allowlist, PII y row-level security por `num_cte` o
  `num_cta` autorizada.
- Data Steward: `evaluar_calidad` compara contra
  `calidad_hallazgos_esperados`; `validar_calidad_datos` usa la clave declarada
  (PK, restricción única o expectativa) y no una heurística por nombre.
- Normatividad: `buscar_evidencia` expone fragmentos crudos y `verificar_cita`
  valida citas; `buscar_normatividad` queda como compatibilidad.
- `tests/`: pruebas deterministas de contrato, SQL/RLS, calidad, evidencia y
  hub. El puente del grafo al hub se activa con `DATA_ANALYST_HUB=1`.

El grafo todavía despacha especialistas, no `PlanTareas`; el puente es una ruta
opcional para comparar el hub con el flujo MCP anterior.

## Siguientes pasos

1. Definir el catálogo de tareas por dominio.
2. Convertir Normatividad en hub con recuperación múltiple y verificación de
   citas.
3. Refactorizar el grafo para despachar tareas, no agentes.
4. Mover las tools actuales a cada hub.
5. MCP persistente y métricas de latencia.
6. Auth real y estado de sesión sin singleton.
