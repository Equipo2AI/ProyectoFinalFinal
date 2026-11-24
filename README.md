# Proyecto — Agente Multi-Agente Bancario

Sistema **Data Steward & Data Analyst** para Banamex. Integra una base sintética,
agentes con herramientas, servidores MCP, recuperación normativa y una interfaz
Streamlit que expone la traza de cada turno.

Se encuentra en estado beta funcional: consulta la base sintética por MCP, ejecuta
SQL de solo lectura y construye la respuesta con el resultado de las herramientas.
Los casos que aún fallan se documentan con su causa.

El proyecto comenzó con un Data Steward sobre `proyecto/data/clientes.csv`. Esa
etapa fue reemplazada por la base sintética SQLite que se describe en este
documento; el historial de esa primera versión permanece en Git.

## Arquitectura

El orquestador ocupa el centro: recibe la consulta, decide qué especialistas
participan y distribuye el trabajo. Ningún especialista invoca a otro; todos
leen y escriben en el estado compartido del grafo.

```text
  ┌───────────────────┐                            ┌───────────────────┐
  │    Data Steward   │                            │    Data Analyst   │
  │ metadatos/calidad │                            │  SQL solo lectura │
  └─────────┬─────────┘                            └─────────┬─────────┘
            │                                                │
            │             ┌───────────────────┐              │
            └─────────────│    Orquestador    │──────────────┘
            ┌─────────────│     LangGraph     │──────────────┐
            │             └───────────────────┘              │
            │                                                │
  ┌─────────▼─────────┐                            ┌─────────▼─────────┐
  │  Normatividad RAG │                            │      Document     │
  │      Pinecone     │                            │    Intelligence   │
  └───────────────────┘                            └───────────────────┘
```

Las conexiones son de ida y vuelta: el orquestador despacha y recibe resultados.
Data Steward y Data Analyst se comunican por MCP `stdio` con
`proyecto/bd/banamex.db`; Normatividad consulta el índice Pinecone; Document
Intelligence utiliza Ollama con Qwen2.5-VL sobre una imagen local. Streamlit
presenta la respuesta y la traza de herramientas.

Las evaluaciones KYC/AML quedan fuera del alcance del MVP actual; el
orquestador las rechaza sin emitir una evaluación regulatoria.

## Requisitos

- Python >= 3.12 y [`uv`](https://docs.astral.sh/uv/).
- Ollama >= 0.40.15 (`mimo-v2.6` requiere esa versión, según los requisitos del equipo).
- Los modelos oficiales por agente se leen de `proyecto/config/models.yaml`;
  ese archivo es la fuente de sus nombres y asignaciones.
- En equipos de 16 GB los modelos compiten por memoria; Ollama descarga de
  memoria el que queda inactivo.
- Claves de Pinecone para el corpus normativo (`PINECONE_API_KEY`, `INDEX_NAME`).

Preparar el entorno Python si aún no existe, desde la raíz del repositorio:

```bash
uv sync
```

Abre Ollama o ejecuta `ollama serve`. Después comprueba los modelos:

```bash
uv run python -m proyecto.config.preparar_modelos
uv run python -m proyecto.config.preparar_modelos --instalar
```

El primer comando comprueba el servicio y los modelos; termina con código 1
si faltan modelos y 2 si hay un error de conexión o configuración. El segundo
instala solo los faltantes con sus nombres oficiales y verifica el resultado.
Las descargas ocupan varios GB. Abre Ollama (o ejecuta `ollama serve`) antes.
Se respeta `OLLAMA_HOST` si está definido. Los mismos comandos funcionan en
macOS, Linux y PowerShell con `uv` instalado.

`.env.example` sirve como plantilla de `.env`. `DATABASE_URL` es opcional: si no
se define, se usa la ruta de `proyecto/config/data_connection.yaml`. `MCP_TIMEOUT` (segundos, 180 por defecto)
limita cada llamada MCP `stdio` para que una tool colgada no bloquee la UI. El
valor por defecto cubre el arranque en frío de Ollama; si tu equipo es más lento,
súbelo en `.env`. Las llamadas de documentos usan `MCP_DOCUMENT_TIMEOUT` (1,800
segundos por defecto) porque un PDF escaneado puede tardar varios minutos en
OCR.

## Base sintética

La base SQLite se regenera desde el script SQL:

```bash
uv run python proyecto/bd/cargar_bd.py
```

El comando crea `proyecto/bd/banamex.db` con las tablas y vistas que utilizan el
Data Steward y el Data Analyst. El archivo está en `.gitignore`: en un clon
nuevo, el cargador debe ejecutarse antes de consultar datos.

Las herramientas del Data Steward calculan conteos de estructura y calidad. No
envían al modelo filas ni valores personales: reportan nombres de columnas, tipos
detectados y cantidades agregadas. Un identificador repetido se reporta para
revisión, no como instrucción para borrar datos. La clave de duplicados sale de
la PK/restricción única o de `calidad_hallazgos_esperados`, no de una heurística
por nombre. `evaluar_calidad` compara lo encontrado contra esos hallazgos
esperados y marca coincidencias y diferencias. Si una consulta clara de otro
dominio llega al Data Steward, responde con su alcance y redirige al especialista
adecuado; el enrutamiento principal sigue en el orquestador.

## Interfaz Streamlit

Streamlit forma parte de las dependencias del proyecto. Desde la raíz:

```bash
uv run streamlit run proyecto/ui/app.py
```

El panel de traza muestra, por turno, la herramienta MCP invocada, sus
argumentos, el estado, la consulta SQL, la gráfica y las fuentes. La interfaz
también admite imágenes para OCR o descripción y documentos adjuntos (PDF, DOCX,
XLSX, PPTX, CSV, HTML y texto) para convertirlos a Markdown. Con una sesión
autenticada, `document_intelligence` solo puede leer archivos dentro de
`DOCUMENT_UPLOAD_ROOT` (por defecto `proyecto/data/uploads`), aislados por
identidad; las rutas locales arbitrarias se rechazan.

La interfaz muestra la respuesta, la traza de agentes y las fuentes recuperadas.

## Agentes, modelos y herramientas

| Agente | Descripción | Modelo | Herramientas MCP |
|---|---|---|---|
| `orchestrator` | Enruta la consulta y compone la respuesta | `mimo-v2.6` | — |
| `data_steward` | Gobierno y calidad de datos bancarios | `qwen2.5:7b-instruct` | `inspeccionar_dataset`, `validar_calidad_datos`, `evaluar_calidad` |
| `data_analyst` | Análisis cuantitativo sobre datos bancarios | `mimo-v2.6` | `analizar_datos`, `listar_tablas`, `describir_tabla`, `valores_columna`, `validar_sql`, `ejecutar_consulta`, `graficar_consulta`, `catalogo_metricas`, `consultar_metrica`, `resolver_periodo`, `resolver_valor`, `verificar_cifras` |
| `normatividad_rag` | Búsqueda semántica sobre documentación | `llama3.1:latest` | `buscar_normatividad`, `buscar_evidencia`, `verificar_cita` |
| `document_intelligence` | Lectura de imágenes y documentos | `qwen2.5vl:7b` | `extraer_texto_imagen`, `describir_imagen`, `convertir_documento_markdown` |

`convertir_documento_markdown` usa **MarkItDown** para DOCX, XLSX, PPTX, CSV,
HTML y texto. Los PDF se procesan por página: si tienen capa de texto se extrae
directamente; si una página está escaneada o tiene poco texto, se rasteriza y se
transcribe con **OCR local** (`DOCUMENT_PDF_MODEL`, por defecto
`qwen2.5vl:3b`). El resultado conserva marcas `## Página N` y se guarda en el
contexto privado. `DOCUMENT_PDF_OCR_MAX_PAGES` limita cuántas páginas sin texto
se procesan por PDF (20 por defecto); las páginas de texto nativo no consumen
ese límite. `DOCUMENT_PDF_DPI` controla la resolución de rasterizado (150 por
defecto). Si el PDF excede el límite, se puede indicar un rango en el mensaje de
carga, por ejemplo «lee las páginas 21-30». El resultado de OCR, descripción o
Markdown se entrega al orquestador,
que decide qué responder al cliente en lugar de mostrar el texto crudo
directamente. Los formatos no soportados y los archivos que exceden el límite
(10 MB en imágenes, 25 MB en documentos) se rechazan con un mensaje legible
que lista los formatos permitidos; el servicio no propaga el traceback a la UI.

El texto completo de cada documento se guarda en un SQLite local
(`proyecto/data/document_context.db`), aislado por el usuario autenticado
(`subject`). Los archivos originales se guardan bajo
`DOCUMENT_UPLOAD_ROOT` y la UI los elimina al limpiar la conversación o cerrar sesión.
**No se indexan en Pinecone ni se comparten con otros usuarios.** El
orquestador recibe un contexto acotado: el documento activo completo si cabe, o
un resumen extractivo más las secciones relevantes. La retención y los límites
se configuran con `DOCUMENT_CONTEXT_TTL_DAYS`, `DOCUMENT_CONTEXT_MAX_DOCS`,
`DOCUMENT_CONTEXT_MAX_CHARS` y `DOCUMENT_CONTEXT_SUMMARY_CHARS`.

En el Data Analyst, `analizar_datos` recibe la pregunta en lenguaje natural y la
resuelve con las herramientas SQL (`listar_tablas`, `describir_tabla`,
`valores_columna`, `validar_sql` y `ejecutar_consulta`). `validar_sql` revisa el
AST con `sqlglot`: una sola sentencia, catálogo real, solo lectura, PII y
row-level security (`num_cte` del cliente o una `num_cta` que le pertenezca).
Las tools auxiliares también aplican el alcance del perfil. El agente usa
`mimo-v2.6`: en la práctica, `qwen2.5-coder:7b` describió las tablas pero no
ejecutó las herramientas SQL. Cuando `ejecutar_consulta` o `consultar_metrica`
devuelve filas, el backend se detiene y presenta ese resultado; no sigue
explorando otras dimensiones ni métricas. Si LangGraph alcanza su límite de
pasos, devuelve un mensaje para reformular la pregunta en lugar de propagar el
error de recursión.

El backend del Data Analyst cachea el esquema (`listar_tablas`, `describir_tabla`)
y el catálogo de métricas en `proyecto/data/data_analyst_metadata_cache.json`.
Como cada llamada MCP arranca un subproceso nuevo, el caché vive en disco y se
comparte entre consultas; la clave incluye la versión de esquema de SQLite y los
permisos PII de la sesión. No se cachean filas, valores ni conteos: el número de
filas de `describir_tabla` se recalcula con el alcance actual. El TTL se controla
con `DATA_ANALYST_METADATA_CACHE_TTL` (3,600 segundos por defecto) y el caché se
desactiva con `DATA_ANALYST_METADATA_CACHE=0`. El backend inyecta un resumen
compacto de tablas, columnas y métricas en el prompt para que el agente no tenga
que pedir `listar_tablas`, `describir_tabla` ni `catalogo_metricas` en cada
consulta; se puede desactivar con `DATA_ANALYST_PROMPT_CATALOG=0`.

`graficar_consulta` no ejecuta código generado por el modelo: valida y ejecuta
una consulta agregada y dibuja el PNG con matplotlib en el backend. Rechaza
identificadores, datos personales, resultados truncados, ejes no numéricos y
categorías repetidas. El grafo la genera cuando el usuario pide una gráfica; la
UI la muestra y permite descargarla desde `Respuesta.grafica`.

Para evitar SQL libre en preguntas frecuentes, `catalogo_metricas` declara
métricas cerradas (clientes, cuentas, saldos, transacciones e importes brutos o
netos) con sus dimensiones y `consultar_metrica` las ejecuta con allowlist. Los
saldos se marcan como `snapshot`: la consulta debe indicar un corte
(`anio_mes`/`cve_fecha_informacion`) o agrupar por `anio_mes`; así no se suman
cortes distintos. `resolver_periodo` convierte expresiones como `2026-02`,
`1T 2026` o `mes pasado` a claves de `dim_fecha`; `resolver_valor` normaliza una
categoría antes del `WHERE`. `verificar_cifras` contrasta cifras y PII de la
redacción contra la evidencia de la última consulta.

La capa semántica adaptada a SQLite vive en `proyecto/semantic/`. Valida tablas,
vistas, columnas, métricas, reglas y relaciones contra `banamex.db`:

```bash
.venv/bin/python -m proyecto.semantic.validar
.venv/bin/python -m proyecto.semantic.validar --json
```

No modifica la base ni las respuestas por sí sola: es el contrato que mantiene
alineados el esquema real, las vistas `vw_*` y el catálogo de métricas.

El orquestador usa `mimo-v2.6` para la planificación y la síntesis.

## Integración MCP

Todos los especialistas se exponen como servidores MCP y el grafo los invoca por
`stdio`, con un subproceso por llamada:

| Servidor | Archivo |
|---|---|
| Data Steward | `proyecto/mcp_servers/data_steward_server.py` |
| Data Analyst | `proyecto/mcp_servers/data_analyst_server.py` |
| Normatividad | `proyecto/mcp_servers/normatividad_rag_server.py` |
| Document Intelligence | `proyecto/mcp_servers/document_intelligence_server.py` |

El orquestador no describe la base en el prompt: solicita metadatos o ejecuta SQL
y devuelve el resultado de la herramienta.

## RAG de gobernanza

El agente `normatividad_rag` responde únicamente con evidencia recuperada:

- **Corpus** (`data_governance_files/`): Ley Federal de Protección de Datos
  Personales en Posesión de los Particulares, Ley de Protección y Defensa al
  Usuario de Servicios Financieros, Ley para Regular las Sociedades de
  Información Crediticia, Circular Única de Bancos, Aviso de Privacidad Integral
  de Banamex, Aviso legal y Términos y condiciones Citibanamex.
- **Índice**: Pinecone (`INDEX_NAME`), embeddings `llama-text-embed-v2`,
  fragmentos de 1200 caracteres con 150 de traslape y 3 documentos finales.
- **Recuperación híbrida**: recupera hasta `RAG_CANDIDATES` candidatos (12 por
  defecto), combina el score semántico con cobertura léxica y coincidencia de
  fuente, deduplica fragmentos y exige `RAG_MIN_SCORE`/`RAG_MIN_COVERAGE`/
  `RAG_MIN_SOURCE_MATCH`. Si un término distintivo no aparece en el corpus,
  devuelve `sin_evidencia` en lugar de evidencia tangencial. Los scores quedan
  en la traza, no en la respuesta final.
- **Respuesta final**: si el RAG aporta evidencia, el orquestador agrega una
  sección `Evidencia normativa recuperada` con los fragmentos literales y sus
  fuentes, aunque la síntesis multiagente los haya resumido. Los scores quedan
  en la traza; si no hay evidencia, la respuesta lo declara.
- **Prompt**: la evidencia es la única fuente válida; prohíbe usar conocimiento
  previo, inventar artículos o plazos, y obliga a citar archivo y página. Si la
  evidencia no alcanza, la respuesta lo declara.
- **Enrutamiento**: las preguntas normativas de alta confianza (leyes, privacidad,
  avisos, políticas) se envían directo al servidor de normatividad con la
  subpregunta normativa, para que el modelo no reformule la pregunta y pierda
  artículos. Las preguntas informativas sobre Banamex/Citibanamex también van a
  RAG; si además piden datos (conteos, saldos, transacciones), se conserva la
  ruta analítica. Una pregunta general sobre una afore sin marca no se fuerza a
  RAG.
- **Evidencia cruda**: `buscar_evidencia` devuelve fragmentos con `fuente`,
  `pagina`/`fila` y `cita_id` sin sintetizar. `verificar_cita` comprueba que una
  cita textual exista en la evidencia recuperada; `buscar_normatividad` se
  conserva por compatibilidad. Las tres reutilizan la misma recuperación
  cacheada por consulta: no repiten la búsqueda en Pinecone.

Para reindexar el corpus:

```bash
uv run python data_governance_files/upload_files.py
```

El RAG cubre preguntas de un solo tema. La relación entre la normativa y los
datos almacenados no está resuelta; se detalla en el diagnóstico.

## Diagnóstico y formulación de consultas

### Diagnóstico

El análisis funcional señala que el origen principal de los fallos está en el
orquestador, no en los especialistas:

1. El plan híbrido cubre consultas multi-intención y conserva las rutas
   deterministas cuando el planner LLM omite un dominio. Las reglas ya no se
   quedan con el primer especialista.
2. El verificador comprueba que el especialista respondió, pero todavía no que
   el SQL cubra exactamente el producto o el periodo pedidos.
3. Ya existe un nodo de aclaración: si falta un dato que cambiaría la respuesta,
   el grafo pregunta y no ejecuta especialistas. La primera versión es
   determinista y cubre periodos relativos, gráficas sin métrica, saldos
   ambiguos y documentos sin archivo.
4. Los modelos de 7-9B sostienen bien una herramienta aislada, pero pierden el
   hilo en consultas SQL largas o en inferencias.

Las conclusiones son preliminares y se basan en la observación del flujo actual.
La siguiente iteración profundiza en la suficiencia de los resultados y en las
ambigüedades que no cubren las reglas.

### Formulación de consultas

Los mejores resultados se obtienen cuando la consulta es explícita y nombra la
tabla o la herramienta:

| Vago | Explícito |
|---|---|
| ¿Cuál es la estructura de las tablas? | Inspecciona la tabla `raw_clientes` con el Data Steward. |
| ¿Cómo va lo de los clientes? | ¿Cuántos clientes hay por tipo de persona en la base sintética? |
| ¿Y lo de la ley? | ¿Qué dice la Ley Federal de Protección de Datos Personales sobre el consentimiento? |

En general, una consulta con una sola idea, la tabla nombrada y el agente o la
herramienta explícitos produce mejores resultados.

## Orquestación en grafo

`proyecto/orchestration/grafo.py` implementa un `StateGraph` de LangGraph con
estado compartido:

```text
interpretar -> planear -> ejecutar -> verificar
   |             ^                         |
   |             └── reintento (máximo 1) ──┘
   |                                        |
   └──── aclaración (sin especialistas) ─ sintetizar
```

- `interpretar` lee la consulta antes de planear. Si detecta una ambigüedad
  material (por ejemplo, «últimos meses» sin una ventana), responde con una
  pregunta y opciones, y no ejecuta especialistas. Si el supuesto es seguro, lo
  declara en la respuesta para que el usuario pueda corregirlo.
- `planear` produce un plan de **capacidades**, no solo una lista de agentes.
  Cada paso declara `id`, `capacidad`, `args` y `depende_de`. El planner recibe
  el catálogo de `proyecto/orchestration/capacidades.py` y puede combinar
  capacidades para consultas nuevas. Las reglas léxicas siguen siendo pistas y
  el plan determinista es el respaldo si el LLM falla o devuelve un plan
  inválido. No se pueden inventar capacidades, ejecutar efectos que no sean de
  lectura ni saltarse dependencias.
- `ejecutar` corre los pasos en orden topológico, comparte resultados y
  artefactos entre pasos, y deja las fuentes en `resultados`/`artefactos` para
  que la síntesis no las pierda. Si un paso falla, sus dependientes no se
  ejecutan.
- `verificar` revisa si todos los especialistas del plan respondieron; si alguno
  falló, reintenta una vez. El reintento es único: el fallo típico de un modelo
  pequeño es de planificación, y repetir el mismo plan no lo corrige, solo suma
  latencia. Si el reintento también falla, el sintetizador responde con lo que sí
  llegó y deja el error visible en la traza.
- `sintetizar` compone la respuesta final. Si hay un solo especialista, conserva
  su salida verificada; si hay varios, el orquestador integra las respuestas y
  las fuentes en una sola explicación. Si el modelo no está disponible, cae a la
  presentación por bloques.
- Antes de formatear, el nodo de Data Steward valida el contrato de las tools
  (tipos y rangos de los agregados). Una salida fuera de contrato se reporta como
  error y no se publica.
- El estado guarda `interpretacion`, `aclaracion`, `supuestos`, `plan`,
  `pasos`, `subpreguntas`, `resultados`, `artefactos`, `evidencia`, `trazas`,
  `errores` e `intentos`.
- `ORCHESTRATION_PLANNER` controla el planner:
  - `hybrid` (por defecto): el LLM piensa toda consulta sustantiva y las reglas
    son pistas/respaldo; solo evita la llamada en saludos o aclaraciones muy
    cortas.
  - `capabilities`: fuerza el planner de capacidades, con respaldo determinista.
  - `llm`: alias de `capabilities`.
  - `rules`: conserva solo las reglas anteriores, sin planner LLM.
- `ORCHESTRATION_SYNTHESIS` controla la respuesta multiagente:
  - `auto` (por defecto): el orquestador integra varios resultados.
  - `llm`: fuerza la síntesis con LLM.
  - `rules`: concatena los bloques por especialista.

La comunicación entre agentes es por estado compartido, no por llamadas directas
entre servidores MCP. Así el verificador y el sintetizador pueden ver la evidencia
de todos los especialistas sin crear una malla de dependencias.

Para agregar una capacidad nueva, se registra en
`proyecto/orchestration/capacidades.py`, se conecta su ejecutor en
`_EJECUTORES` (o se expone como tool MCP) y se agregan pruebas. El planner la
descubre desde el catálogo; no hace falta agregar una ruta léxica por cada frase.
Las capacidades deben ser de solo lectura y declarar sus entradas, salidas,
permisos y requisitos.

## Estructura

```text
proyecto/
├── config/
│   ├── models.yaml           # Modelo por agente y OCR de PDF (fuente efectiva)
│   ├── data_connection.yaml  # Fuente de datos (SQLite banamex.db)
│   └── memory.yaml           # Memoria de largo plazo (desactivada)
├── bd/
│   ├── crear_bd.sql          # Script de la base sintética de Banamex
│   └── cargar_bd.py          # Genera banamex.db
├── agents/
│   ├── data_steward_agent.py
│   ├── normatividad_rag_agent.py
│   ├── data_analyst_agent.py
│   ├── document_intelligence_agent.py
│   └── pdf_reader.py         # Texto nativo + OCR por página de PDF
├── mcp_servers/              # Servidores MCP por especialista
├── orchestration/
│   ├── capacidades.py        # Catálogo de capacidades y contrato de pasos
│   ├── planificador.py       # Planner LLM por capacidades con fallback
│   ├── comun.py              # Helpers compartidos (MCP, normalización)
│   ├── grafo.py              # Grafo LangGraph
│   └── orquestador.py        # Adaptador entre UI y grafo
├── ui/                       # Streamlit, estilos y delta del tema claro
├── servicio.py               # Contrato entre UI y orquestador
└── main.py                   # CLI
```

## Configuración

La conexión a datos se toma de `proyecto/config/data_connection.yaml`, que apunta a
`proyecto/bd/banamex.db`. Cuando `DATABASE_URL` está definida, tiene prioridad; si
falta el YAML o `data_source.path`, el arranque falla con un mensaje claro.

La memoria de largo plazo se configura en `proyecto/config/memory.yaml`, que es la
fuente de todos sus parámetros, y está desactivada por defecto. Es independiente
del contexto documental privado, que vive en
SQLite local y se recorta por presupuesto de caracteres antes de llegar al
modelo. El historial de chat también tiene una ventana propia
(`CHAT_HISTORY_MESSAGES`, `CHAT_HISTORY_CHARS`).

El modelo de cada agente y el de OCR de PDF se toman de
`proyecto/config/models.yaml`; es la única fuente de nombres. Si falta el
archivo o una entrada, el arranque falla con un mensaje claro.
`DOCUMENT_INTELLIGENCE_MODEL` sobrescribe el modelo de visión de imágenes y
`DOCUMENT_PDF_MODEL` el de OCR de PDF.

## Límites conocidos

Estos límites corresponden a la arquitectura actual:

- El planner por capacidades cubre combinaciones nuevas de las capacidades
  registradas; si el LLM falla o devuelve un plan inválido, se usa el plan
  determinista. Las capacidades que todavía no existen no se inventan: el
  orquestador debe pedir aclaración o declarar el límite.
- El nodo de aclaración es determinista: cubre patrones comunes, pero una
  ambigüedad no prevista puede llegar al especialista. En ese caso el Data
  Analyst debe pedir aclaración o declarar el supuesto.
- El verificador valida que el especialista respondió, no que el contenido exista
  en el esquema ni que el SQL haya aplicado todos los filtros. Si no puede
  confirmar un filtro de producto o de periodo, añade una nota visible; aun así
  pueden aparecer columnas inexistentes o resultados más amplios de lo pedido.
- Los modelos de 7-9B resuelven bien una herramienta aislada, pero pierden
  consistencia en SQL largo o en inferencias entre fuentes.
- El OCR de PDF está limitado a 20 páginas sin texto por archivo. Un PDF
  escaneado de 20 páginas puede tardar unos 15 minutos en un Mac de 16 GB; el
  texto nativo se extrae sin OCR y es mucho más rápido.

## Siguientes pasos

1. **Verificador de suficiencia**: contrastar que el SQL y el resultado cubran
   el producto, el periodo y los filtros pedidos antes de publicar la respuesta.
2. **Aclaraciones más generales**: ampliar el nodo determinista con un intérprete
   LLM para ambigüedades que no cubren los patrones actuales, manteniendo el
   fallback y el límite de latencia.
3. **Verificador contra esquema**: contrastar las tablas y columnas citadas con
   los metadatos antes de publicar la respuesta.
4. **Comparación de modelos**: medir 14B y 32B con la misma arquitectura para
   evaluar el efecto en planificación e inferencia.


## Acceso local y modo demo

La interfaz exige login y usa las identidades existentes en AppData. El modo
visual `demo` del equipo se conserva como opcion de presentacion, pero nunca
crea un auditor anonimo ni concede acceso a todos los clientes. El valor
predeterminado es `PROYECTO_AUTH_MODE=local`; incluso si se indica `demo`, los
permisos de datos requieren una sesion autenticada real.

Se conservan `render_login`, `identidad_actual` y `cerrar_sesion` para los
consumidores del componente del equipo. La pantalla principal usa `render_access`
con tema, validacion por rerun y borrado del historial al cerrar sesion.
No hay selector de rol: el operador aprovisiona identidades y permisos.

```bash
uv run python -m proyecto.security.local_auth --usuario usuario_prueba --rol normal --clientes 1
uv run python -m proyecto.security.local_auth --usuario auditor_prueba --rol auditor --clientes 1,2,25 --permisos audit,aggregate,governance,pii:nombre,pii:rfc
```

Estos son ejemplos de asignacion del operador, no identidades precargadas:
confirma primero que la fuente es de prueba y que existe autorizacion para
esos clientes. La clave se pide por consola (minimo 16 caracteres), se deriva
con PBKDF2-SHA256 y no se versiona. Para consultas globales el operador debe
otorgar explicitamente todo el alcance necesario; `aggregate` no amplia el
conjunto de clientes. `audit` habilita auditorias dentro de ese conjunto y los
campos personales necesitan permisos adicionales. El chat nunca modifica roles.

La sesion opaca dura una hora y puede revocarse al cerrar sesion. Esta ligada
al archivo y SHA-256 de SQLite: si cambia la base hay que reprovisionar una
identidad autorizada; no se reutiliza automaticamente contra otra fuente.
En Windows, los registros privados y la auditoria se guardan fuera del repositorio
y de OneDrive, en `%LOCALAPPDATA%\ProyectoFinalFinal\auth\<id-del-proyecto>`.
La consola, Streamlit y los procesos MCP calculan la misma ruta para este proyecto.
Ejecuta todos con tu usuario habitual de Windows, sin administrador. Puedes consultar
la ubicacion efectiva sin leer credenciales:

```powershell
.\.venv\Scripts\python.exe -m proyecto.security.local_auth --ruta
```

`PROYECTO_AUTH_DIR` permite configurar otra carpeta privada; si lo usas, debe tener
el mismo valor en la consola y en Streamlit. No compartas ese directorio, claves,
tokens ni capturas con datos personales. La carpeta antigua `.local_auth/` no se
borra ni se alteran sus permisos y sigue excluida de Git. Si puedes leer legitimamente
su registro, `--migrar-desde <ruta-a-identities.json>` importa identidades con sus
hashes y permisos sin sobrescribir identidades distintas ni importar sesiones.
Tras cambiar de ubicacion, reinicia Streamlit desde tu consola habitual.

Ejecuta el servicio solo en localhost para desarrollo:

```bash
uv run streamlit run proyecto/ui/app.py --server.address 127.0.0.1
```

Las consultas de Data Analyst usan una proyeccion SQLite efimera en memoria,
limitada por `dim_cuenta.num_cte -> dim_cliente.num_cte` y por `num_cta` en
saldos y movimientos. El SQL no puede acceder al archivo fuente ni usar ATTACH
ni escrituras. La fuente se abre en mode=ro; no se modifica ni regenera.
Data Steward exige `governance` para sus agregados de calidad. Los historiales
son por sesion. Las consultas sensibles registran sujeto, rol, alcance resumido
y hash SQL, sin nombres, cuentas, consultas literales ni resultados personales.
Se desactiva el trazado externo durante consultas autenticadas sensibles.

Este mecanismo confia en el operador y en el acceso al sistema de archivos del
usuario de Windows. No es un proveedor de identidad de produccion: quien puede
editar el registro o leer la base fuera de la aplicacion pertenece a esa frontera
de confianza. Aprovisiona fuera de sesiones activas; para despliegues compartidos
se necesita un IdP, gestion de secretos, almacenamiento transaccional de sesiones
y controles de acceso del sistema operativo.


### Respuestas verificadas de las preguntas bancarias

Data Analyst reconoce intenciones acotadas de productos, saldos, movimientos,
gastos, crecimiento, agregados y auditoria. Construye SELECT de solo lectura
con periodos e identificadores tipados y ejecuta la herramienta SQL existente
sobre el alcance autenticado. La tabla visible y el SQL proceden de esa ejecucion,
sin una segunda reformulacion que cambie filas o cifras. No hay cuentas bloqueadas
globalmente. Las consultas no reconocidas conservan el agente general con el
modelo oficial; sus llamadas tienen limite de espera.

Normatividad intenta Pinecone con espera acotada. Si no esta disponible,
recupera fragmentos literales de los PDF/TXT reales de `data_governance_files`,
indicando archivo, pagina y modo local. El fallback léxico usa `pypdf`, que es
dependencia declarada del proyecto; el lector principal de PDF usa `pypdfium2`.
Esto no certifica conectividad Pinecone ni vigencia legal externa del corpus.
Las respuestas locales no usan conocimiento inventado del modelo.

El gobierno de datos se adapta explícitamente a `raw_clientes` y `raw_saldos`
de SQLite, no a recreaciones de `clientes.csv`. Las herramientas reportan
conteos de negativos y clasificación PII por nombres del esquema, sin mostrar
valores individuales.


### Login visual y sesiones

La pantalla de acceso usa la autenticacion local anterior, sin otro registro ni
selector de permisos. Aprovisiona tu usuario con el comando de la seccion
"Autenticacion local de desarrollo" (la clave se pide por consola; no hay una
clave de demostracion fija). Abre Streamlit con el Python de `.venv` y entra con
ese identificador y clave. El login y el chat permiten cambiar entre claro y
oscuro. Cerrar sesion revoca el token y descarta el historial; una sesion vencida
vuelve al acceso. Los permisos se validan de nuevo en el backend y MCP.

### Mitigacion provisional de MiMo en Windows

Se reprodujo el error CUDA en Ollama 0.40.16 durante el calentamiento de Flash
Attention. Con `use_mmap=true` se observaron una carga directa y otra a traves
del orquestador exitosas por GPU. Esto es una mitigacion observada, **no una
reparacion garantizada del fallo intermitente** ni una prueba de causa unica.
No se cambiaron controladores, modelos, servicio ni configuracion de Windows.

`proyecto/config/ollama_runtime.py` envia esa opcion dentro de `options` de la
solicitud para MiMo en Windows; el constructor original de la version instalada
de LangChain no la serializa por si solo. Los demas modelos conservan sus
opciones. Para desactivar la mitigacion solo en la terminal actual:

```powershell
$env:PROYECTO_OLLAMA_MMAP = "0"
```

Para volver al comportamiento de la aplicacion, elimina esa variable de la
terminal (`Remove-Item Env:PROYECTO_OLLAMA_MMAP`). No uses `setx` ni cambies `.env`.
Referencias: [carga predeterminada de Ollama en Windows/CUDA](https://github.com/ollama/ollama/blob/main/server/sched.go)
y [reporte de fallo intermitente de memoria del host](https://github.com/ollama/ollama/issues/17380).
La similitud de ese reporte con los registros locales no demuestra que sea
la misma causa en este equipo.


### Autenticación local

Las métricas y gráficas se conservan con autenticación obligatoria. El `perfil`
recibido por API/MCP orienta el flujo pero no concede roles: el alcance y los
permisos provienen de la sesión real de AppData. No se habilita ningún auditor
anónimo.

La evidencia y los límites posteriores a la integración se conservan fuera del
repositorio. La autenticación usa identidades reales y no habilita acceso
anónimo.
