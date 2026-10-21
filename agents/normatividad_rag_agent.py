# Normatividad / RAG Agent — Semantic search in documents.
# Domain: policies, manuals, business rules, semantic memory.


class NormatividadRAGAgent:
    def __init__(self):
        self.nombre = "normatividad_rag"
        self.descripcion = "Búsqueda semántica sobre documentación normativa (RAG)."
        self.modelo = "qwen2.5:7b-instruct"
        self.herramientas = []

    def procesar(self, consulta: str) -> str:
        return f"[{self.nombre}] Procesando: {consulta}"

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


normatividad_rag_agent = NormatividadRAGAgent()
