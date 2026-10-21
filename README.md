# Proyecto — Agente Multi-Agente Bancario

## Estructura de archivos

```
proyecto/
├── config/
│   ├── models.yaml           # Modelo por agente
│   └── data_connection.yaml  # Fuente de datos
├── data/
│   ├── clientes.csv
│   └── bank_metadata_simple.json
├── agents/
│   ├── data_steward_agent.py
│   ├── normatividad_rag_agent.py
│   ├── data_analyst_agent.py
│   ├── document_intelligence_agent.py
│   └── compliance_agent.py
├── orchestration/
│   └── orquestador.py
├── main.py
├── requirements.txt
├── .env.example
└── README.md
```

## Agentes

| Agente | Descripción | Modelo | Herramientas |
|--------|-------------|--------|--------------|
| data_steward | Gobierno y calidad de datos bancarios |  | |
| normatividad_rag | Búsqueda semántica sobre documentación |  | |
| data_analyst | Análisis cuantitativo sobre datos bancarios |  | |
| document_intelligence | Lectura de imágenes y documentos |  | |
| compliance | Reglas regulatorias: KYC, AML | | |
