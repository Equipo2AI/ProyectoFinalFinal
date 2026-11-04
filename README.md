# Proyecto — Agente Multi-Agente Bancario

Demo de un asistente bancario con un orquestador LangChain, servidores MCP y una
interfaz Streamlit. El orquestador decide qué especialista atiende cada consulta
y la UI muestra la respuesta y la traza de herramientas.

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

## Interfaz Streamlit

```bash
uv run --with streamlit streamlit run proyecto/ui/app.py
```

Preguntas de ejemplo de la pantalla inicial:

1. ¿Qué medidas de seguridad exige la Ley Federal de Protección de Datos
   Personales en Posesión de los Particulares?
2. ¿Cuántos clientes hay en la base sintética y cómo se distribuyen por tipo de
   persona?
3. ¿Qué dice el aviso de privacidad de Banamex sobre el tratamiento de datos
   personales?

La interfaz permite adjuntar imágenes para OCR o descripción. Mantén el panel de
traza abierto para ver la herramienta MCP, sus argumentos, el estado, la consulta
SQL y las fuentes.

## CLI

```bash
# Lista los agentes registrados
python proyecto/main.py

# Consulta al Data Steward sobre la base sintética
python proyecto/main.py --consulta "¿Qué problemas de calidad tiene raw_clientes?"
```

## Agentes y servidores MCP

| Agente | Servidor MCP | Estado |
|---|---|---|
| `data_steward` | `data_steward_server.py` | Estructura y calidad de la base sintética; solo agregados. |
| `normatividad_rag` | `normatividad_rag_server.py` | Recuperación Pinecone y respuesta con fuentes. |
| `data_analyst` | `data_analyst_server.py` | SQL de solo lectura con herramientas genéricas; valida que la respuesta use el SQL ejecutado. |
| `document_intelligence` | `document_intelligence_server.py` | OCR y descripción de imágenes locales. |
| `compliance` | — | Fase 2; el agente es un stub y no tiene servidor. |

El orquestador invoca los servidores MCP por `stdio` y usa `qwen2.5:7b-instruct`
para enrutar. Las preguntas normativas de alta confianza (leyes, privacidad,
avisos, políticas) se envían directo al servidor de normatividad con el texto
original para evitar que el modelo reformule la consulta y pierda artículos.

## Estructura relevante

```
proyecto/
├── agents/                  # Agentes y configuración compartida de la base
├── bd/                      # crear_bd.sql y cargar_bd.py
├── mcp_servers/             # Servidores MCP por especialista
├── orchestration/           # Orquestador y prompt de enrutamiento
├── ui/                      # Streamlit, estilos y componentes de traza
├── servicio.py              # Contrato entre UI y orquestador
└── main.py                  # CLI
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

## Límites actuales

- `compliance`/KYC/AML está fuera de esta fase.
- El estado del orquestador es global por proceso; no hay aislamiento por usuario.
- Cada llamada MCP levanta un subproceso nuevo; no hay cliente MCP persistente.
- La memoria de largo plazo está desactivada por defecto en `config/memory.yaml`.
- La primera codificación de una imagen nueva con Qwen2.5-VL puede tardar varios
  minutos por el comportamiento de Ollama/Metal; las siguientes suelen ser rápidas.
