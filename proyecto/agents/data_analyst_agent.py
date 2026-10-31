# Data Analyst Agent — Quantitative analysis and queries.
# Domain: SQL queries, metrics, transactional data visualization.


class DataAnalystAgent:
    def __init__(self):
        self.nombre = "data_analyst"
        self.descripcion = "Análisis cuantitativo sobre datos transaccionales bancarios."
        self.modelo = "llama3.1:latest"
        self.herramientas = []

    def procesar(self, consulta: str) -> str:
        return f"[{self.nombre}] Procesando: {consulta}"

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_analyst_agent = DataAnalystAgent()
