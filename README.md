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

`compliance` (KYC/AML) dispone de un servidor MCP de demostración y el grafo lo
utiliza para rechazar ese tipo de consultas; las reglas reales quedan para fase 2.

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
se define, se usa la ruta de `proyecto/config/data_connection.yaml` y, en su
defecto, `proyecto/bd/banamex.db`. `MCP_TIMEOUT` (segundos, 180 por defecto)
limita cada llamada MCP `stdio` para que una tool colgada no bloquee la UI. El
valor por defecto cubre el arranque en frío de Ollama; si tu equipo es más lento,
súbelo en `.env`.

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
esperados y marca coincidencias y diferencias.

## Interfaz Streamlit

Streamlit se instala al vuelo para no cargar el entorno base. Desde la raíz:

```bash
uv run --with streamlit streamlit run proyecto/ui/app.py
```

El panel de traza muestra, por turno, la herramienta MCP invocada, sus
argumentos, el estado, la consulta SQL, la gráfica y las fuentes. La interfaz
también admite imágenes para OCR o descripción.

Ejemplo de una consulta de normatividad con evidencia y fuentes:

![Consulta de normatividad en la interfaz, con la traza y las fuentes a la derecha](files/consulta.png)

## Agentes, modelos y herramientas

| Agente | Descripción | Modelo | Herramientas MCP |
|---|---|---|---|
| `orchestrator` | Enruta la consulta y compone la respuesta | `mimo-v2.6` | — |
| `data_steward` | Gobierno y calidad de datos bancarios | `qwen2.5:7b-instruct` | `inspeccionar_dataset`, `validar_calidad_datos`, `evaluar_calidad` |
| `data_analyst` | Análisis cuantitativo sobre datos bancarios | `mimo-v2.6` | `analizar_datos`, `listar_tablas`, `describir_tabla`, `valores_columna`, `validar_sql`, `ejecutar_consulta`, `graficar_consulta`, `catalogo_metricas`, `consultar_metrica`, `resolver_periodo`, `resolver_valor`, `verificar_cifras` |
| `normatividad_rag` | Búsqueda semántica sobre documentación | `llama3.1:latest` | `buscar_normatividad`, `buscar_evidencia`, `verificar_cita` |
| `document_intelligence` | Lectura de imágenes y documentos | `qwen2.5vl:7b` | `extraer_texto_imagen`, `describir_imagen` |
| `compliance` | Reglas regulatorias: KYC, AML | `qwen2.5:7b-instruct` | `revisar_compliance_demo` (demostración, fase 2) |

En el Data Analyst, `analizar_datos` recibe la pregunta en lenguaje natural y la
resuelve con las herramientas SQL (`listar_tablas`, `describir_tabla`,
`valores_columna`, `validar_sql` y `ejecutar_consulta`). `validar_sql` revisa el
AST con `sqlglot`: una sola sentencia, catálogo real, solo lectura, PII y
row-level security (`num_cte` del cliente o una `num_cta` que le pertenezca).
Las tools auxiliares también aplican el alcance del perfil. El agente usa
`mimo-v2.6`: en las pruebas, `qwen2.5-coder:7b` describió las tablas pero no
ejecutó las herramientas SQL.

`graficar_consulta` no ejecuta código generado por el modelo: valida y ejecuta
una consulta agregada y dibuja el PNG con matplotlib en el backend. Rechaza
identificadores, datos personales, resultados truncados, ejes no numéricos y
categorías repetidas. El grafo y el hub pueden generarla cuando el usuario pide
una gráfica; la UI la muestra y permite descargarla desde `Respuesta.grafica`.

Para evitar SQL libre en preguntas frecuentes, `catalogo_metricas` declara
métricas cerradas (clientes, saldo, transacciones, importes) con sus dimensiones
y `consultar_metrica` las ejecuta con allowlist. `resolver_periodo` convierte
expresiones como `2026-02`, `1T 2026` o `mes pasado` a claves de `dim_fecha`;
`resolver_valor` normaliza una categoría antes del `WHERE`. `verificar_cifras`
contrasta cifras y PII de la redacción contra la evidencia de la última consulta.

El hub nuevo de Data Analyst vive en `proyecto/orchestration/hubs.py` y usa esas
tools de bajo nivel, sin `analizar_datos`. Para compararlo con el flujo MCP
anterior en el grafo:

```bash
DATA_ANALYST_HUB=1 uv run python -c \
  "from proyecto.orchestration.grafo import ejecutar_grafo; print(ejecutar_grafo('¿Cuántos clientes hay?')['respuesta'])"
```

El orquestador usa `mimo-v2.6` para las respuestas directas; en modo `hub`
también enruta cuando las reglas deterministas no alcanzan.

## Integración MCP

Todos los especialistas se exponen como servidores MCP y el grafo los invoca por
`stdio`, con un subproceso por llamada:

| Servidor | Archivo |
|---|---|
| Data Steward | `proyecto/mcp_servers/data_steward_server.py` |
| Data Analyst | `proyecto/mcp_servers/data_analyst_server.py` |
| Normatividad | `proyecto/mcp_servers/normatividad_rag_server.py` |
| Document Intelligence | `proyecto/mcp_servers/document_intelligence_server.py` |
| Compliance | `proyecto/mcp_servers/compliance_server.py` (demostración, fase 2) |

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
  fragmentos de 1200 caracteres con 150 de traslape y `k = 3` documentos.
- **Prompt**: la evidencia es la única fuente válida; prohíbe usar conocimiento
  previo, inventar artículos o plazos, y obliga a citar archivo y página. Si la
  evidencia no alcanza, la respuesta lo declara.
- **Enrutamiento**: las preguntas normativas de alta confianza (leyes, privacidad,
  avisos, políticas) se envían directo al servidor de normatividad con el texto
  original, para que el modelo no reformule la pregunta y pierda artículos.
- **Evidencia cruda**: `buscar_evidencia` devuelve fragmentos con `fuente`,
  `pagina`/`fila` y `cita_id` sin sintetizar. `verificar_cita` comprueba que una
  cita textual exista en la evidencia recuperada; `buscar_normatividad` se
  conserva por compatibilidad.

Para reindexar el corpus:

```bash
uv run python data_governance_files/upload_files.py
```

El RAG cubre preguntas de un solo tema. La relación entre la normativa y los
datos almacenados no está resuelta; se detalla en el diagnóstico.

## Casos de prueba

La tabla siguiente resume los resultados de la demo del 4 de noviembre. **Sí**
indica respuesta correcta y **No**, fallo.

### Casos que funcionan

| Pregunta | Agente | Resultado |
|---|---|---|
| ¿Qué dice la Ley Federal de Protección de Datos Personales en Posesión de los Particulares? | Normatividad | Sí: cita artículos y fuente (archivo y página). |
| ¿Qué dice la ley de protección de datos por privados? | Normatividad | Sí: el enrutamiento de alta confianza reconoce la variante y responde con la misma evidencia. |
| ¿Qué dice el aviso de privacidad de Banamex sobre el tratamiento de datos personales? | Normatividad | Sí: recupera el aviso y responde con citas. |
| ¿Cuál es el promedio de los sueldos? | Data Analyst | Sí: se enruta como agregado y responde. La base sintética no tiene columna `sueldo`; la respuesta debe declarar el proxy utilizado o la ausencia del dato. |
| ¿Qué problemas de calidad tiene `raw_clientes`? | Data Steward | Sí: nombra la tabla y devuelve faltantes, duplicados y claves repetidas reales. |
| Adjuntar una factura y pedir el texto | Document Intelligence | Sí: transcribe el contenido visible con OCR. |
| ¿Y en enero? (después de una consulta de saldos) | Data Analyst | Sí: la memoria de corto plazo conserva el hilo y reutiliza el contexto. |

### Casos que fallan

| Pregunta | Agente | Síntoma | Causa |
|---|---|---|---|
| ¿Cuál es la estructura de las tablas? | Data Steward | No: alucina y agrega columnas que no existen, por ejemplo "puntaje de buró de crédito". | Sin tabla explícita, el verificador no contrasta la salida contra el esquema real y la síntesis rellena el hueco. |
| ¿Qué relación tiene la ley de protección de datos con los datos almacenados? | Normatividad (solo) | No: no conecta los dos puntos; se queda en la fase de recuperación del RAG. | Falta un plan multiagente que cruce evidencia normativa con metadatos. El plan actual enruta a un solo especialista. |
| Consultas SQL complejas (varios joins, condiciones o pasos) | Data Analyst | No: el ciclo no termina; se corta antes de verificar y sintetizar. | El orquestador de 9B no sostiene el plan; el verificador reintenta una sola vez y no hay descomposición en pasos. |
| Preguntas donde el agente debe pedir una aclaración al usuario | Cualquiera | No: se interrumpe el turno. | No existe un nodo de aclaración: el grafo no puede suspender la ejecución para preguntar y retomar. |
| Preguntas que exigen inferir entre dos ideas | Cualquiera | No: responde una parte o cambia de tema. | El sintetizador compone bloques, no razona sobre ellos; no hay contraste entre evidencias. |

### Diagnóstico

El análisis de los casos anteriores señala que el origen principal de los fallos
está en el orquestador, no en los especialistas:

1. El plan es determinista y de un solo paso; cuando la pregunta necesita dos
   fuentes, elige una y descarta la otra.
2. El verificador comprueba que el especialista respondió, pero no que lo dicho
   exista en el esquema; de ahí que aparezcan columnas inventadas.
3. No hay nodo de aclaración, por lo que una pregunta incompleta interrumpe el
   ciclo en lugar de volver al usuario.
4. Los modelos de 7-9B sostienen bien una herramienta aislada, pero pierden el
   hilo en consultas SQL largas o en inferencias.

Las conclusiones son preliminares y se basan en los casos observados. La
siguiente iteración abordará esta parte del orquestador.

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

El contrato de tareas, resultados y hubs de dominio vive en
`proyecto/orchestration/tareas.py` y `proyecto/orchestration/hubs.py`. Es el
diseño que permite que el orquestador delegue objetivos de alto nivel y cada hub
decida qué herramientas usar.

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
- `verificar` revisa si todos los especialistas respondieron; si alguno falló,
  reintenta una vez. El reintento es único: el fallo típico de un modelo pequeño
  es de planificación, y repetir el mismo plan no lo corrige, solo suma latencia. Si
  el reintento también falla, el sintetizador responde con lo que sí llegó y deja
  el error visible en la traza.
- `sintetizar` compone la respuesta final. Si hay un solo especialista, conserva
  su salida verificada; si hay varios, presenta cada bloque con su agente.
- Antes de formatear, el nodo de Data Steward valida el contrato de las tools
  (tipos y rangos de los agregados). Una salida fuera de contrato se reporta como
  error y no se publica.
- El estado guarda `plan`, `resultados`, `trazas`, `errores` e `intentos`.
- El modo se controla con `ORCHESTRATION_MODE`:
  - `graph` (por defecto): usa LangGraph.
  - `hub`: usa el orquestador anterior como respaldo.
  - Si el grafo falla, se intenta el hub para no dejar la interfaz sin respuesta.

La comunicación entre agentes es por estado compartido, no por llamadas directas
entre servidores MCP. Así el verificador y el sintetizador pueden ver la evidencia
de todos los especialistas sin crear una malla de dependencias.

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

## Pruebas

La suite automatizada se ejecuta con:

```bash
uv run pytest -q
```

Cubre el guard SQL, RLS por identidad, métricas, periodos, gráficas, hubs,
tareas y autenticación local.

Las pruebas manuales (`tests/probar_*.py`, `tests/rag_test.py` y la imagen de
factura) no se publican en el repositorio; se conservan localmente para
desarrollo. Los servidores MCP también pueden verificarse de forma aislada desde
esos scripts locales.

## Configuración

La conexión a datos se toma de `config/data_connection.yaml`, que apunta a
`proyecto/bd/banamex.db`. Cuando `DATABASE_URL` está definida, tiene prioridad.

La memoria de largo plazo se configura en `config/memory.yaml` y está desactivada
por defecto.

El modelo de cada agente se toma de `config/models.yaml` al inicializar el agente.
Si el archivo o la entrada faltan, se usa el valor por defecto de
`proyecto/config/models.py`. `DOCUMENT_INTELLIGENCE_MODEL` sobrescribe el modelo
de visión.

## Límites conocidos

Estos límites corresponden a la arquitectura y a las pruebas con agentes:

- El plan es determinista y de un solo paso: ante preguntas que requieren dos
  fuentes, prioriza una y descarta la otra.
- El verificador valida que el especialista respondió, no que el contenido exista
  en el esquema; de ahí que puedan aparecer columnas inexistentes.
- No existe un nodo de aclaración: una pregunta incompleta interrumpe el ciclo en
  lugar de volver al usuario.
- Los modelos de 7-9B resuelven bien una herramienta aislada, pero pierden
  consistencia en SQL largo o en inferencias entre fuentes.
- La suite automatizada cubre contratos y seguridad; las pruebas de calidad de
  respuesta con LLM siguen siendo manuales y dependen de Ollama/Pinecone.

## Siguientes pasos

1. **Plan multi-paso**: descomponer las preguntas que cruzan normativa y datos, y
   ejecutar los especialistas en secuencia.
2. **Nodo de aclaración**: permitir que el grafo suspenda el turno, pregunte y
   retome la ejecución.
3. **Verificador contra esquema**: contrastar las tablas y columnas citadas con
   los metadatos antes de publicar la respuesta.
4. **Suite de evaluación**: convertir los casos de evaluación en pruebas
   repetibles por agente, con criterios de aceptación.
5. **Comparación de modelos**: medir 14B y 32B con la misma suite para evaluar el
   efecto en planificación e inferencia.


## Acceso local y modo demo

Para esta etapa la interfaz arranca sin login con `PROYECTO_AUTH_MODE=demo`
(valor por defecto). Usa una identidad sintética con alcance completo sobre la
base de prueba: no crea usuarios ni contraseñas. No uses este modo con datos
reales.

El componente de login sigue disponible en `proyecto/ui/login.py` para que el
equipo de frontend lo integre. Para probarlo sin alterar el flujo principal,
abre la app con `?login=1`. Para exigir login en toda la app, define
`PROYECTO_AUTH_MODE=local` y aprovisiona identidades. Ahí solo se escriben
usuario y clave; no se pone la contraseña de la base ni una API key. El acceso
identifica a la persona, limita filas y tablas a su alcance y registra
auditoría. No hay selector de rol: el operador local aprovisiona las identidades
y sus permisos usando IDs de clientes existentes:

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
`.local_auth/` contiene registros privados y auditoria y esta excluido de Git.
No compartas ese directorio, claves, tokens ni capturas con datos personales.

Ejecuta el servicio solo en localhost para desarrollo:

```bash
uv run --with streamlit streamlit run proyecto/ui/app.py --server.address 127.0.0.1
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
y controles de acceso del sistema operativo. No se ampliaron los permisos de
Compliance ni sus capacidades de demostracion.


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
indicando archivo, pagina y modo local. El lector PDF usa `pypdf`, ya presente
en el entorno; si falta, debe prepararse esa dependencia antes de leer PDF.
Esto no certifica conectividad Pinecone ni vigencia legal externa del corpus.
Las respuestas locales no usan conocimiento inventado del modelo.

Las pruebas de gobierno de datos son adaptaciones explicitas a `raw_clientes`
y `raw_saldos` de SQLite, no recreaciones de `clientes.csv`. Las herramientas
reportan conteos de negativos y clasificacion PII por nombres del esquema,
sin mostrar valores individuales.

Pruebas focalizadas de autorizacion (fixture aislada, sin Ollama ni datos reales):

```bash
uv run python -m unittest discover -s tests -p test_local_authorization.py -v
```
