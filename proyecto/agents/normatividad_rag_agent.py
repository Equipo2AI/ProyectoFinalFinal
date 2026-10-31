# Normatividad / RAG Agent — Semantic search in documents.
# Domain: policies, manuals, business rules, semantic memory.

import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore

load_dotenv()

SYSTEM_PROMPT = """
Eres un especialista en normatividad: políticas, manuales, reglas de negocio
y lineamientos internos.

Usa la herramienta de búsqueda normativa siempre que la pregunta dependa de:
- políticas o lineamientos
- procedimientos y manuales
- reglas de negocio
- requisitos, restricciones o excepciones
- responsables, plazos o vigencias

Basa tus afirmaciones normativas únicamente en la evidencia recuperada.
Si la evidencia es insuficiente, contradictoria o no autoriza lo consultado,
dilo explícitamente. No inventes permisos ni requisitos.

Cuando sea posible, menciona el nombre del archivo fuente que respalda
la respuesta. Responde en el idioma de la consulta.
"""


class NormatividadRAGAgent:
    def __init__(self, k: int = 3):
        self.nombre = "normatividad_rag"
        self.descripcion = "Búsqueda semántica sobre documentación normativa (RAG)."
        self.modelo = "llama3.1:latest"
        self.modelo_embeddings = "llama-text-embed-v2"  # debe coincidir con el usado al indexar
        self.k = k

        # Los componentes pesados se crean al primer uso, para que importar
        # este módulo dentro de la arquitectura mayor no requiera Pinecone/Ollama.
        self._retriever = None
        self._agente = None
        self.herramientas = [self._crear_herramienta_busqueda()]

    def _get_retriever(self):
        if self._retriever is None:
            # Embeddings hospedados por Pinecone (usa PINECONE_API_KEY del entorno).
            vector_store = PineconeVectorStore(
                index_name=os.environ["INDEX_NAME"],
                embedding=PineconeEmbeddings(model=self.modelo_embeddings),
            )
            self._retriever = vector_store.as_retriever(
                search_kwargs={"k": self.k},
            )
        return self._retriever

    def _crear_herramienta_busqueda(self):
        @tool
        def buscar_normatividad(query: str) -> str:
            """Busca en la documentación normativa: políticas, manuales, reglas de negocio y lineamientos."""

            docs = self._get_retriever().invoke(query)

            if not docs:
                return "No se encontró evidencia normativa relevante."

            evidencia = []
            for doc in docs:
                fuente = doc.metadata.get("source", "desconocida")
                pagina = doc.metadata.get("page")
                fila = doc.metadata.get("row")

                ubicacion = fuente
                if pagina is not None:
                    ubicacion += f", página {pagina}"
                if fila is not None:
                    ubicacion += f", fila {fila}"

                evidencia.append(f"FUENTE: {ubicacion}\n{doc.page_content}")

            return "\n\n---\n\n".join(evidencia)

        return buscar_normatividad

    def _get_agente(self):
        if self._agente is None:
            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agente = create_agent(
                model=llm,
                tools=self.herramientas,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agente

    # Interfaz pública

    @staticmethod
    def _extraer_texto(resultado) -> str:
        mensaje = resultado["messages"][-1]

        if isinstance(mensaje.content, str):
            return mensaje.content

        return "".join(
            bloque.get("text", "")
            for bloque in mensaje.content
            if isinstance(bloque, dict) and bloque.get("type") == "text"
        )

    def procesar(self, consulta: str) -> str:
        resultado = self._get_agente().invoke(
            {"messages": [{"role": "user", "content": consulta}]}
        )
        return self._extraer_texto(resultado)

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


normatividad_rag_agent = NormatividadRAGAgent()