# Evaluación del agente con DeepEval

Suite repetible que envía preguntas al orquestador y califica cada respuesta
contra las **historias de usuario de KEI GPT (US-01 a US-11)**. Además de las
métricas de calidad, cada corrida registra el **rendimiento** (tiempos, CPU,
memoria, tokens/s) y las **especificaciones del equipo** donde se ejecutó.

Las preguntas viven en `casos/*.yaml` (un archivo por historia): **para
agregar, cambiar o borrar un caso solo se edita YAML**, no código.

```text
deepeval/
├── casos/                  # ← los casos, uno por historia (us01…us11)
├── historias.yaml          # catálogo de historias y criterios de aceptación
├── identidades.yaml        # identidades de prueba que la suite aprovisiona
├── agente.py               # único punto de contacto con el proyecto
├── cargador.py             # lee y valida los YAML
├── metricas.py             # métricas (reglas, latencia, juez)
├── identidades.py          # aprovisiona y abre sesión (local_auth)
├── fixtures.py             # adjuntos sintéticos (comprobante, DOCX, CSV, PDF…)
├── rendimiento.py          # equipo, tiempos, recursos y tokens
├── _subproceso/            # hook que mide las llamadas LLM en los MCP
├── reporte.py              # reporte HTML local
├── test_agente.py          # un test por caso
└── conftest.py             # aislamiento y resumen de la corrida
```

> No agregues `__init__.py` a esta carpeta: se llama igual que la librería y
> dejaría de importarse `deepeval`.

Los criterios que se pueden verificar sin LLM (autorización, guard SQL,
herramientas del Data Steward, gráficas, RAG local, archivos, contexto
documental, trazas) tienen además pruebas deterministas en
`tests/historias/` (`uv run pytest tests/historias`, segundos y sin Ollama).

## Instalación (una vez)

```bash
uv sync --all-groups          # incluye deepeval y psutil (grupo dev)
uv run python proyecto/bd/cargar_bd.py
```

Agrega a `.env`:

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=1              # no enviar telemetría de uso
DEEPEVAL_JUEZ=ollama:qwen2.5:7b-instruct  # modelo que califica (opcional)
DEEPEVAL_RESULTS_FOLDER=deepeval/resultados
```

## Ejecución

Desde la raíz del proyecto, con Ollama activo y la base generada:

```bash
# Toda la suite
uv run deepeval test run deepeval/test_agente.py

# Una historia (us01 … us11) o un grupo (smoke, seguridad, documentos…)
uv run deepeval test run deepeval/test_agente.py -m us03
uv run deepeval test run deepeval/test_agente.py -m smoke

# Un caso por id, o los que contengan un texto
uv run pytest deepeval -k us05-modo-local

# Solo verificaciones deterministas, sin LLM juez (rápido)
DEEPEVAL_SOLO_REGLAS=1 uv run deepeval test run deepeval/test_agente.py

# Listar los casos sin ejecutarlos
uv run pytest deepeval --collect-only -q

# Reporte HTML (calidad + rendimiento + equipo)
uv run python deepeval/reporte.py --abrir
```

No uses `-n` (procesos en paralelo): el orquestador guarda el estado de la
conversación, Ollama atiende una petición a la vez y los tiempos medidos
dejarían de ser comparables.

## Identidades y aislamiento

El servicio solo concede permisos a una **sesión real** de `local_auth`; el
`perfil` que viaja con la consulta no otorga nada (US-01). Por eso la suite
actúa como operador: al arrancar crea las identidades de `identidades.yaml`
(`cliente_1`, `cliente_2`, `auditor`, `analista`, `data_steward`) con una
clave aleatoria que no se imprime, abre sesión por caso y la revoca al
terminar. `sin_sesion` ejecuta el caso sin token.

Todo ocurre en una carpeta temporal (`PROYECTO_AUTH_DIR`, contexto documental
y cargas): la suite **nunca toca tu AppData real** ni tu
`document_context.db`. Usa `DEEPEVAL_CONSERVAR_TEMPORAL=1` para inspeccionarla
después de la corrida.

## Formato de un caso

Solo `id` y `pregunta` (o `adjunto`) son obligatorios.

```yaml
predeterminados:            # opcional: aplica a todos los casos del archivo
  identidad: cliente_1
  etiquetas: [data_analyst]

casos:
  - id: us03-saldo-propio-con-periodo   # único en toda la suite
    cubre: [US-03.CA-01, US-03.CA-02]   # historias/criterios (historias.yaml)
    identidad: cliente_1                # ver identidades.yaml; o sin_sesion
    pregunta: ¿Cuál era el saldo de mi cuenta de cheques en junio de 2026?
    adjunto: fixture:estado_cuenta.docx # opcional: imagen o documento
    entorno: {PINECONE_API_KEY: ""}     # variables solo durante el caso
    turnos_previos:                     # conversación previa
      - ¿Cuál es el saldo total por mes?
      - {adjunto: "fixture:comprobante.png", pregunta: Extrae el texto}
      - {limpiar: true}                 # botón "Limpiar"
      - {identidad: cliente_2, pregunta: …}  # turno con otra sesión
    # --- verificaciones deterministas (métrica "reglas", sin LLM) ---
    agente_esperado: data_analyst       # data_steward | data_analyst | normatividad_rag | document_intelligence | orquestador
    agentes_esperados: [normatividad_rag, data_analyst]   # todos deben participar
    herramientas_esperadas: [mcp_analizar_datos]
    debe_contener: ["10,329.53"]        # todos (ignora mayúsculas y acentos)
    debe_contener_alguno: ["no existe", "no hay"]
    no_debe_contener: ["100001"]
    error_esperado: Acceso denegado     # la denegación ES el resultado correcto
    alertas_esperadas: ["No se pudo generar la gráfica"]  # otras alertas fallan
    debe_tener_sql: true                # true/false; omitido = no se revisa
    debe_tener_grafica: true
    debe_tener_fuentes: true
    max_filas: 50                       # filas devueltas a la UI
    sin_pii: true                       # sin correo/teléfono/CURP/RFC (respuesta y traza)
    traza_completa: true                # cada herramienta con nombre y estado
    bd_sin_cambios: true                # el hash de banamex.db no cambia
    identidad_sin_cambios: true         # rol/alcance/permisos intactos
    max_segundos: 30                    # presupuesto de tiempo (métrica "latencia")
    # --- verificaciones con LLM juez ---
    respuesta_esperada: …               # activa "correccion"
    criterio: …                         # activa "criterio"
    metricas: [relevancia, fidelidad]   # por defecto: [relevancia]
    umbral: 0.5
    # --- control ---
    etiquetas: [smoke]                  # se suman a las del archivo y a usNN
    xfail: "motivo"                     # fallo conocido: se reporta sin romper la suite
    skip: "motivo"                      # no se ejecuta
```

Adjuntos `fixture:` disponibles (se generan con datos conocidos en
`fixtures_generados/`, ignorado por Git): `comprobante.png`,
`comprobante.tiff`, `imagen_grande.png` (10.5 MB), `estado_cuenta.docx`,
`movimientos_junio.csv`, `estado_cuenta_escaneado.pdf` (sin capa de texto),
`documento_grande.pdf` (25.5 MB), `programa.exe`.

| Métrica | Qué mide | Necesita |
|---|---|---|
| `reglas` | Agentes, herramientas, textos, errores/alertas esperadas, SQL/gráfica/fuentes, filas, PII, trazas, base e identidad intactas. Siempre corre. | — |
| `latencia` | Que el turno evaluado termine dentro de `max_segundos` (o `DEEPEVAL_MAX_SEGUNDOS`). | — |
| `relevancia` | Que la respuesta atienda la pregunta. | juez |
| `fidelidad` | Que la respuesta se apoye en la salida de las herramientas. | juez |
| `correccion` | Que coincida con `respuesta_esperada`. | juez |
| `criterio` | El criterio libre del caso. | juez |

Un campo mal escrito, un `id` repetido o un `cubre` que no exista en
`historias.yaml` detiene la suite con un mensaje que indica el archivo.

## Rendimiento y equipo

Cada caso se mide sin cambiar el proyecto:

| Dato | Cómo se obtiene |
|---|---|
| Tiempo del agente (turno evaluado), turnos previos y total | reloj de pared alrededor de cada llamada al servicio |
| Tiempo por paso | `duracion` de los `pasos` que reporta el servicio |
| Tiempo del juez | separado del tiempo del agente |
| CPU de pytest y de los subprocesos MCP | `os.times()` (los hijos no se reportan en Windows) |
| Picos de RAM (pytest + MCP, Ollama, sistema, swap), CPU y GPU NVIDIA | muestreo cada 0.5 s con `psutil` y `nvidia-smi` |
| Llamadas LLM: tokens de entrada/salida, tokens/s, tiempo de carga del modelo | callback de LangChain en este proceso y en los subprocesos MCP (`_subproceso/sitecustomize.py`) con los datos que reporta Ollama |
| Modelos cargados y % en GPU | `GET /api/ps` de Ollama al final de cada caso |

Una vez por corrida se registra el equipo: sistema operativo, CPU y núcleos,
RAM, GPU, batería/corriente, versión de Ollama, modelos configurados, juez,
versiones de paquetes y commit de Git. El nombre del equipo **no** se guarda;
se usa un identificador anónimo.

Salidas:

- En el JSON de DeepEval (`test_run_*.json`), cada caso lleva `completionTime`
  (tiempo del agente), `inputTokenCount`/`outputTokenCount` y `metadata` con
  `historias`, `cubre`, `identidad`, `rendimiento` y `equipo`.
- `resultados/rendimiento_<fecha>.json` y `.md`: equipo, percentiles por
  historia y por modelo, picos de recursos, **matriz de cobertura de criterios
  de aceptación** y una fila por caso. Se escribe también con `pytest` simple.
- `reporte.py` muestra tiempo del agente y del juez por caso, tokens/s,
  tiempo por historia, la tarjeta del equipo y el detalle de rendimiento.

| Variable | Efecto | Valor por defecto |
|---|---|---|
| `DEEPEVAL_JUEZ` | Modelo juez, `proveedor:modelo` (`ollama`, `openai`, `gemini`) | `ollama:qwen2.5:7b-instruct` |
| `DEEPEVAL_SOLO_REGLAS` | `1` omite las métricas con LLM | desactivado |
| `DEEPEVAL_MAX_SEGUNDOS` | Presupuesto de tiempo para todos los casos sin `max_segundos` | sin presupuesto |
| `DEEPEVAL_RENDIMIENTO` | `0` desactiva la medición de rendimiento | activado |
| `DEEPEVAL_LLM_STATS` | `0` desactiva el registro de tokens (hook de subprocesos) | activado |
| `DEEPEVAL_RESULTS_FOLDER` | Carpeta de resultados | `.deepeval` (el repo usa `deepeval/resultados`) |
| `DEEPEVAL_CASOS_DIR` | Otra carpeta de casos | `deepeval/casos` |
| `DEEPEVAL_CONSERVAR_TEMPORAL` | `1` conserva la carpeta temporal de identidades/contexto | desactivado |

Para comparar equipos o modelos, ejecuta la misma suite en cada máquina y
abre los `rendimiento_*.md`: el primer caso que usa un modelo incluye su
carga en memoria (`Carga (s)`), así que conviene comparar medianas, no el
total. En batería, el equipo puede reducir frecuencia; la tarjeta de equipo
lo indica.

## Fallos conocidos (`xfail`)

| Caso | Motivo |
|---|---|
| `us03-pii-con-permiso` | `redactar_pii` oculta el RFC aunque la sesión tenga `pii:rfc`. |
| `us08-pregunta-ambigua-pide-aclaracion` | El grafo no tiene nodo de aclaración. |
| `us08-limpiar-descarta-contexto` | "Limpiar" no borra el `DocumentContextStore` del usuario. |

`us10-respaldo-hub` está en `skip`: el checkout no tiene modo hub.

## Cuando el agente cambie

- **Cambia el prompt, el modelo o el grafo:** nada que tocar; vuelve a ejecutar.
- **Un caso `xfail` empieza a pasar:** aparece como `XPASS`; borra su `xfail`.
- **Cambia la forma de invocar al agente o la estructura de la respuesta:**
  ajusta solo `agente.py`.
- **Nueva historia o criterio:** agrégalo a `historias.yaml` y crea
  `casos/usNN_<tema>.yaml`.

## Límites

- `fidelidad` necesita evidencia en la traza: sirve con Data Steward y con
  Normatividad cuando la traza conserva los fragmentos. Sin evidencia se omite.
- Un juez de 7B es ruidoso y a veces no devuelve el JSON que DeepEval espera.
  Las `reglas` son la señal confiable; para calificaciones estables conviene un
  juez más grande en `DEEPEVAL_JUEZ`.
- Los tokens solo cuentan llamadas hechas con LangChain (todas las del
  proyecto lo son). Un hilo propio sin contexto puede quedar sin registrar.
