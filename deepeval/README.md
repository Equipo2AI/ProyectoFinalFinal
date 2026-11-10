# Evaluación del agente con DeepEval

Suite repetible que envía preguntas al orquestador y califica cada respuesta.
Las preguntas viven en `casos/*.yaml`: **para agregar, cambiar o borrar un caso
solo se edita YAML**, no código.

```text
deepeval/
├── casos/               # ← las preguntas (un YAML por especialista)
│   ├── normatividad.yaml
│   ├── data_analyst.yaml
│   ├── data_steward.yaml
│   └── general.yaml
├── agente.py            # único punto de contacto con el proyecto
├── cargador.py          # lee y valida los YAML
├── metricas.py          # métricas y modelo juez
├── test_agente.py       # un test por caso
└── conftest.py          # configuración de pytest
```

> No agregues `__init__.py` a esta carpeta: se llama igual que la librería y
> dejaría de importarse `deepeval`.

## Instalación (una vez)

```bash
uv add --dev deepeval
```

Agrega a `.env`:

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=1              # no enviar telemetría de uso
DEEPEVAL_JUEZ=ollama:qwen2.5:7b-instruct  # modelo que califica (opcional)
DEEPEVAL_RESULTS_FOLDER=deepeval/resultados
```

## Ejecución

Desde la raíz del proyecto, con Ollama activo y la base generada
(`uv run python proyecto/bd/cargar_bd.py`):

```bash
# Toda la suite
uv run deepeval test run deepeval/test_agente.py

# Solo los casos etiquetados "smoke" (o normatividad, data_analyst, seguridad…)
uv run deepeval test run deepeval/test_agente.py -m smoke

# Un caso por id, o los que contengan un texto
uv run pytest deepeval -k norm-consentimiento
uv run pytest deepeval -k "norm or ds"

# Solo verificaciones deterministas, sin LLM juez (rápido)
DEEPEVAL_SOLO_REGLAS=1 uv run deepeval test run deepeval/test_agente.py

# Listar los casos sin ejecutarlos
uv run pytest deepeval --collect-only -q
```

No uses `-n` (procesos en paralelo): el orquestador guarda el estado de la
conversación y Ollama atiende una petición a la vez.

| Variable | Efecto | Valor por defecto |
|---|---|---|
| `DEEPEVAL_JUEZ` | Modelo juez, `proveedor:modelo` (`ollama`, `openai`, `gemini`) | `ollama:qwen2.5:7b-instruct` |
| `DEEPEVAL_SOLO_REGLAS` | `1` omite las métricas con LLM | desactivado |
| `DEEPEVAL_RESULTS_FOLDER` | Carpeta de resultados de DeepEval | `.deepeval` (el repo usa `deepeval/resultados`) |
| `DEEPEVAL_CASOS_DIR` | Otra carpeta de casos | `deepeval/casos` |
| `ORCHESTRATION_MODE` | `graph` o `hub` (del proyecto) | `graph` |

## Formato de un caso

Solo `id` y `pregunta` son obligatorios.

```yaml
predeterminados:            # opcional: aplica a todos los casos del archivo
  agente_esperado: data_analyst
  etiquetas: [data_analyst]

casos:
  - id: da-clientes-por-tipo            # único en toda la suite
    pregunta: ¿Cuántos clientes hay por tipo de persona?
    turnos_previos: [...]               # mensajes anteriores de la conversación
    perfil: {rol: usuario, cliente_id: 1}  # sesión para casos de permisos
    # --- verificaciones deterministas (métrica "reglas", sin LLM) ---
    agente_esperado: data_analyst       # data_steward | data_analyst | normatividad_rag | document_intelligence | orquestador
    herramientas_esperadas: [mcp_analizar_datos]
    debe_contener: ["4,800"]            # todos deben aparecer (ignora mayúsculas y acentos)
    debe_contener_alguno: ["no existe", "no hay"]
    no_debe_contener: ["buró de crédito"]
    # --- verificaciones con LLM juez ---
    respuesta_esperada: Hay 4,800 personas físicas y 200 morales.   # activa "correccion"
    criterio: La respuesta declara qué columna usó.                 # activa "criterio"
    metricas: [relevancia, fidelidad]   # por defecto: [relevancia]
    umbral: 0.5                         # mínimo para aprobar las métricas con LLM
    # --- control ---
    etiquetas: [smoke]                  # se suman a las del archivo; filtran con -m
    xfail: "motivo"                     # fallo conocido: se reporta sin romper la suite
    skip: "motivo"                      # no se ejecuta
```

| Métrica | Qué mide | Necesita |
|---|---|---|
| `reglas` | Agente, herramientas, textos y ausencia de errores. Siempre corre. | — |
| `relevancia` | Que la respuesta atienda la pregunta. | juez |
| `fidelidad` | Que la respuesta se apoye en la salida de las herramientas (alucinaciones). | juez |
| `correccion` | Que coincida con `respuesta_esperada`. | juez |
| `criterio` | El criterio libre del caso. | juez |

Un campo mal escrito o un `id` repetido detiene la suite con un mensaje que
indica el archivo.

## Cuando el agente cambie

- **Cambia el prompt, el modelo o el grafo:** nada que tocar; vuelve a ejecutar.
- **Un caso `xfail` empieza a pasar:** aparece como `XPASS`; borra su `xfail`.
- **Cambia la forma de invocar al agente o la estructura de la respuesta:**
  ajusta solo `agente.py`.
- **Nuevo especialista o herramienta:** crea `casos/<nombre>.yaml`.

## Resultados

El consolidado de la última corrida de 30 casos está en
[`resultados/consolidado_20261110.md`](resultados/consolidado_20261110.md).

También se publican las corridas completas de 18 y 12 casos:

- [`resultados/test_run_20261109_215032.json`](resultados/test_run_20261109_215032.json) — 18 casos.
- [`resultados/test_run_20261110_122956.json`](resultados/test_run_20261110_122956.json) — 12 casos.

El resto de los JSON de ejecución queda local e ignorado.

## Límites

- `fidelidad` solo es útil con Data Steward. Normatividad y Data Analyst
  devuelven una respuesta ya sintetizada y el grafo no expone los fragmentos
  recuperados ni las filas del SQL; sin esa evidencia la métrica se omite. Para
  medir el RAG (fidelidad, precisión del contexto) hay que exponer la evidencia
  en la respuesta del servicio y mapearla en `agente.py`.
- Un juez de 7B es ruidoso y a veces no devuelve el JSON que DeepEval espera.
  Las `reglas` son la señal confiable; para calificaciones estables conviene un
  juez más grande en `DEEPEVAL_JUEZ`.

# Para ver los resultados en una web local
uv run python deepeval/reporte.py --abrir

#Metricas usadas:

reglas: checks fixed conditions without any LLM: the expected agent answered, the expected tools were called, required text appears, forbidden text is absent, and the agent reported no errors. It always runs, and every condition must hold to pass.
relevancia: the judge model checks whether the answer actually addresses the question asked, rather than drifting to something else. It does not check whether the answer is true.
fidelidad: the judge checks whether the answer’s claims are supported by what the tools returned, which catches invented columns or figures. Today it only runs for Data Steward questions, because the other agents don’t expose their evidence.
correccion: the judge compares the answer with the respuesta_esperada you wrote, penalizing different figures or missing key facts but not different wording. It runs only on questions that have a respuesta_esperada.
criterio: the judge scores the answer against a rule you write in plain language for that one question, for example “se niega y no muestra ningún saldo”. It runs only on questions that have a criterio line.