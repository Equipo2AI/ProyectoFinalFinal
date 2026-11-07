# Proyecto — Agente Multi-Agente Bancario · Sprint 2

Beta funcional del **Data Steward & Data Analyst** para Banamex: base sintética,
agentes con herramientas, integración MCP, RAG de gobernanza y una interfaz
Streamlit que muestra la respuesta junto con la traza de herramientas.

> **Estado:** beta funcional. El agente consulta la base sintética por MCP, ejecuta
> SQL y usa los resultados para responder. Los casos que todavía fallan están
> documentados más abajo junto con su causa.

## Arquitectura

```text
Usuario
  │
  ▼
Streamlit (chat + panel de traza)
  │
  ▼
Orquestador (grafo LangGraph)
  ├── Data Steward ──────────► MCP ──► banamex.db   (metadatos y calidad)
  ├── Data Analyst ──────────► MCP ──► banamex.db   (SQL de solo lectura)
  ├── Normatividad RAG ──────────────► Pinecone     (leyes, políticas, avisos)
  └── Document Intelligence ─────────► OCR / visión (imágenes locales)
```

`compliance` (KYC/AML) existe como stub y queda para fase 2: no está conectado al
grafo ni tiene servidor MCP.

## Qué cubre esta entrega

| Entregable Sprint 2 | Estado |
|---|---|
| Base sintética generada con los scripts SQL de Banamex | ✅ `proyecto/bd/crear_bd.sql` + `cargar_bd.py` |
| Agente que consulta y analiza la base con herramientas | ✅ Data Steward y Data Analyst sobre `banamex.db` |
| Integración MCP para acceso a datos y servicios | ✅ 4 servidores funcionales por `stdio` (+ `compliance` en fase 2) |
| RAG de gobernanza | ✅ Pinecone con 7 documentos y citas de fuente |
| Al menos cinco casos de prueba documentados | ✅ abajo, con aciertos, fallos y causas |
| Diagrama de arquitectura | ✅ arriba |
| Repo con código, dependencias e instrucciones | ✅ `pyproject.toml` + `uv.lock` |
| Demo que consume la base de verdad | ✅ las respuestas usan el resultado de las herramientas |

## Requisitos

- Python >= 3.12 y [`uv`](https://docs.astral.sh/uv/)
- Ollama con los modelos:
  - `qwen2.5:7b-instruct` (orquestador y Data Analyst)
  - `llama3.1:latest` (Data Steward y Normatividad)
  - `qwen2.5vl:7b` (lectura de imágenes; `qwen2.5vl:3b` es una alternativa ligera)
- Claves de Pinecone para el corpus normativo (`PINECONE_API_KEY`, `INDEX_NAME`)

```bash
uv sync
ollama serve
ollama pull qwen2.5:7b-instruct
ollama pull llama3.1:latest
ollama pull qwen2.5vl:7b
```

Copia `.env.example` a `.env` y completa las claves. `DATABASE_URL` es opcional:
si no existe, se usa la ruta de `proyecto/config/data_connection.yaml`; si tampoco
está configurada, se usa `proyecto/bd/banamex.db`.

## Base sintética

La base SQLite demo se regenera desde el script SQL:

```bash
python proyecto/bd/cargar_bd.py
```

El comando crea `proyecto/bd/banamex.db` con las tablas y vistas que usan el
Data Steward y el Data Analyst. El archivo está en `.gitignore`; en un clon
nuevo hay que ejecutar el cargador antes de consultar datos.

El Data Steward puede revisar la base con herramientas que calculan conteos de
estructura y calidad. Las herramientas no envían al modelo filas ni valores
personales: reportan nombres de columnas, tipos detectados y cantidades
agregadas. Un identificador repetido se reporta para revisión, no como
instrucción para borrar datos.

## Interfaz Streamlit

```bash
uv run --with streamlit streamlit run proyecto/ui/app.py
```

Mantén el panel de traza abierto: ahí se ve la herramienta MCP invocada, sus
argumentos, el estado, la consulta SQL y las fuentes. La interfaz también permite
adjuntar imágenes para OCR o descripción.

## CLI

```bash
# Lista los agentes registrados
python proyecto/main.py

# Consulta al Data Steward sobre la base sintética
python proyecto/main.py --consulta "¿Qué problemas de calidad tiene raw_clientes?"
```

## Agentes, modelos y herramientas

| Agente | Descripción | Modelo | Herramientas MCP |
|---|---|---|---|
| `data_steward` | Gobierno y calidad de datos bancarios | `llama3.1:latest` | `inspeccionar_dataset`, `validar_calidad_datos` |
| `data_analyst` | Análisis cuantitativo sobre datos bancarios | `qwen2.5:7b-instruct` | `analizar_datos`, `listar_tablas`, `describir_tabla`, `valores_columna`, `ejecutar_consulta` |
| `normatividad_rag` | Búsqueda semántica sobre documentación | `llama3.1:latest` | `buscar_normatividad` |
| `document_intelligence` | Lectura de imágenes y documentos | `qwen2.5vl:7b` | `extraer_texto_imagen`, `describir_imagen` |
| `compliance` | Reglas regulatorias: KYC, AML | `llama3.1:latest` | — (fase 2) |

El orquestador usa `qwen2.5:7b-instruct` para enrutar cuando las reglas
deterministas no alcanzan.

## Integración MCP

Todos los especialistas se exponen como servidores MCP y el grafo los invoca por
`stdio`, un subproceso por llamada:

| Servidor | Archivo |
|---|---|
| Data Steward | `proyecto/mcp_servers/data_steward_server.py` |
| Data Analyst | `proyecto/mcp_servers/data_analyst_server.py` |
| Normatividad | `proyecto/mcp_servers/normatividad_rag_server.py` |
| Document Intelligence | `proyecto/mcp_servers/document_intelligence_server.py` |
| Compliance | `proyecto/mcp_servers/compliance_server.py` (sin tools, fase 2) |

La demo se sostiene en esto: el orquestador no describe la base en el prompt,
pide metadatos o ejecuta SQL y devuelve lo que la herramienta respondió.

## RAG de gobernanza

El agente `normatividad_rag` responde únicamente con evidencia recuperada:

- **Corpus** (`data_governance_files/`): Ley Federal de Protección de Datos
  Personales en Posesión de los Particulares, Ley de Protección y Defensa al
  Usuario de Servicios Financieros, Ley para Regular las Sociedades de
  Información Crediticia, Circular Única de Bancos, Aviso de Privacidad Integral
  de Banamex, Aviso legal y Términos y condiciones Citibanamex.
- **Índice**: Pinecone (`INDEX_NAME`), embeddings `llama-text-embed-v2`,
  fragmentos de 1200 caracteres con 150 de traslape y `k = 3` documentos.
- **Prompt**: la evidencia es la única fuente válida; prohíbe usar conocimiento
  previo, inventar artículos o plazos, y obliga a citar archivo y página. Si la
  evidencia no alcanza, lo dice.
- **Enrutamiento**: las preguntas normativas de alta confianza (leyes, privacidad,
  avisos, políticas) se envían directo al servidor de normatividad con el texto
  original, para que el modelo no reformule la pregunta y pierda artículos.

Para reindexar el corpus:

```bash
python data_governance_files/upload_files.py
```

**Límite importante:** el RAG responde bien preguntas de un solo tema. No cruza
la norma con los datos almacenados; eso hoy no está resuelto (ver casos abajo).

## Casos de prueba

Observaciones de la demo del 4 de noviembre. ✅ responde bien · ❌ falla.

### Casos que funcionan

| Pregunta | Agente | Resultado |
|---|---|---|
| ¿Qué dice la Ley Federal de Protección de Datos Personales en Posesión de los Particulares? | Normatividad | ✅ Cita artículos y fuente (archivo y página). |
| ¿Qué dice la ley de protección de datos por privados? | Normatividad | ✅ El enrutamiento de alta confianza reconoce la variante y responde con la misma evidencia. |
| ¿Qué dice el aviso de privacidad de Banamex sobre el tratamiento de datos personales? | Normatividad | ✅ Recupera el aviso y responde con citas. |
| ¿Cuál es el promedio de los sueldos? | Data Analyst | ✅ Se enruta como agregado y responde. **Ojo:** la base sintética no tiene columna `sueldo`, así que hay que verificar que declare el proxy o la ausencia del dato, no que invente una cifra. |
| ¿Qué problemas de calidad tiene `raw_clientes`? | Data Steward | ✅ Nombra la tabla y devuelve faltantes, duplicados y claves repetidas reales. |
| Adjuntar una factura y pedir el texto | Document Intelligence | ✅ Transcribe el contenido visible con OCR. |
| ¿Y en enero? (después de una consulta de saldos) | Data Analyst | ✅ La memoria de corto plazo conserva el hilo y reutiliza el contexto. |

### Casos que fallan

| Pregunta | Agente | Síntoma | Causa |
|---|---|---|---|
| ¿Cuál es la estructura de las tablas? | Data Steward | ❌ Alucina: agrega columnas que no existen, por ejemplo "puntaje de buró de crédito". | Sin tabla explícita, el verificador no contrasta la salida contra el esquema real y la síntesis rellena el hueco. |
| ¿Qué relación tiene la ley de protección de datos con los datos almacenados? | Normatividad (solo) | ❌ No conecta los dos puntos; se queda en la fase de recuperación del RAG. | Falta un plan multi-agente que cruce evidencia normativa con metadatos. El plan actual enruta a un solo especialista. |
| Consultas SQL complejas (varios joins, condiciones o pasos) | Data Analyst | ❌ El ciclo no termina: se corta antes de verificar y sintetizar. | El orquestador de 7B no sostiene el plan; el verificador reintenta una sola vez y no hay descomposición en pasos. |
| Preguntas donde el agente debe pedir una aclaración al usuario | Cualquiera | ❌ Se rompe el turno. | No existe un nodo de aclaración: el grafo no puede suspender la ejecución para preguntar y retomar. |
| Preguntas que exigen inferir entre dos ideas | Cualquiera | ❌ Responde una parte o cambia de tema. | El sintetizador compone bloques, no razona sobre ellos; no hay contraste entre evidencias. |

### Diagnóstico

El fallo principal está en el **orquestador**, no en los especialistas:

1. El plan es determinista y de un solo paso; cuando la pregunta necesita dos
   fuentes, elige una y descarta la otra.
2. El verificador comprueba que el especialista respondió, pero **no que lo
   dicho exista en el esquema**; por eso pasan columnas inventadas.
3. No hay nodo de aclaración, así que cualquier pregunta incompleta rompe el
   ciclo en lugar de volver al usuario.
4. Los modelos de 7B sostienen bien una tool aislada, pero pierden el hilo en
   consultas SQL largas o en inferencias.

### Cómo preguntar para que acierte

Funciona mejor cuando la consulta es explícita y, sobre todo, cuando nombra la
herramienta o la tabla:

| ❌ Vago | ✅ Explícito |
|---|---|
| ¿Cuál es la estructura de las tablas? | Inspecciona la tabla `raw_clientes` con el Data Steward. |
| ¿Cómo va lo de los clientes? | ¿Cuántos clientes hay por tipo de persona en la base sintética? |
| ¿Y lo de la ley? | ¿Qué dice la Ley Federal de Protección de Datos Personales sobre el consentimiento? |

Regla práctica: **una idea por pregunta**, nombrar la tabla y decir qué agente o
herramienta se quiere usar.

## Orquestación en grafo

`proyecto/orchestration/grafo.py` implementa un `StateGraph` de LangGraph con
estado compartido:

```text
planear -> despachar -> especialista -> despachar -> ... -> verificar
   ^                                                         |
   └──────────────── reintento (máximo 1) ──────────────────┘
                                                             |
                                                        sintetizar
```

- `planear` decide rutas con reglas deterministas y auditables.
- `despachar` ejecuta los especialistas en orden y comparte el estado.
- `verificar` revisa si todos los especialistas pidieron respuesta; si falló
  alguno, reintenta una vez.
- `sintetizar` compone la respuesta final. Si hay un solo especialista, conserva
  su salida verificada; si hay varios, presenta cada bloque con su agente.
- El estado guarda `plan`, `resultados`, `trazas`, `errores` e `intentos`.
- El modo se controla con `ORCHESTRATION_MODE`:
  - `graph` (por defecto): usa LangGraph.
  - `hub`: usa el orquestador anterior como respaldo.
  - Si el grafo falla, se intenta el hub para no dejar la UI sin respuesta.

La comunicación entre agentes es por estado compartido, no por llamadas
directas entre servidores MCP. Así el verificador y el sintetizador pueden ver
la evidencia de todos los especialistas sin crear una malla de dependencias.

## Estructura

```text
proyecto/
├── config/
│   ├── models.yaml           # Modelo por agente (documentación; ver Configuración)
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
│   └── compliance_agent.py
├── mcp_servers/              # Servidores MCP por especialista
├── orchestration/
│   ├── grafo.py              # Grafo LangGraph
│   └── orquestador.py        # Adaptador de modos y hub de respaldo
├── ui/                       # Streamlit, estilos y panel de traza
├── servicio.py               # Contrato entre UI y orquestador
└── main.py                   # CLI
```

## Pruebas manuales

No hay pytest ni CI. Los bancos de prueba llaman a Ollama/Pinecone reales:

```bash
python tests/probar_data_steward.py --caso 1
python tests/probar_orquestador_mcp.py --consulta "¿Qué problemas de calidad tiene raw_clientes?"
python tests/probar_document_intelligence.py --caso 1
python tests/rag_test.py --caso 1 --sin-evidencia
```

Los archivos `tests/probar_data_analyst_mcp.py` y los scripts de `mcp_servers/`
también permiten probar los servidores de forma aislada.

## Configuración

- `config/data_connection.yaml` **sí se lee**: define la ruta de `banamex.db`.
  `DATABASE_URL` tiene prioridad.
- `config/memory.yaml` **sí se lee**: memoria de largo plazo, desactivada por
  defecto.
- `config/models.yaml` **hoy no se lee**: documenta el modelo de cada agente,
  pero los valores están definidos en el código de cada uno. Cambiarlo no tiene
  efecto hasta centralizar la carga (ver siguientes pasos).

## Límites conocidos

- `compliance`/KYC/AML está fuera de esta fase.
- El orquestador no puede pedir aclaraciones ni reanudar un turno.
- El verificador no contrasta las respuestas contra el esquema de la base.
- No hay plan multi-paso para preguntas que cruzan dominios.
- `document_intelligence` transcribe y describe la imagen, pero no analiza el
  contenido ni lo valida contra datos bancarios.
- El estado del orquestador es global por proceso; no hay aislamiento por usuario.
- Cada llamada MCP levanta un subproceso nuevo; no hay cliente MCP persistente.
- La primera codificación de una imagen nueva con Qwen2.5-VL puede tardar varios
  minutos por el comportamiento de Ollama/Metal; las siguientes suelen ser rápidas.

## Siguientes pasos

1. **Probar modelos con mayor capacidad de razonamiento** (por ejemplo
   `qwen2.5:14b`/`32b`) y comparar contra los 7B actuales con los mismos casos.
2. **Centralizar los modelos** en `config/models.yaml` para cambiarlos sin tocar
   código, con `MODEL_<AGENTE>` en `.env` como override.
3. **Nodo de aclaración**: permitir que el grafo pregunte al usuario y retome.
4. **Verificador contra esquema**: si la respuesta nombra una columna, comprobar
   que exista en la tabla antes de publicarla.
5. **Plan multi-paso** para preguntas que cruzan normativa y datos.
