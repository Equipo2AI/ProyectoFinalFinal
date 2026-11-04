# Normatividad / RAG Agent — Semantic search in documents.
# Domain: policies, manuals, business rules, semantic memory.

import os
import re

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore

load_dotenv()

SYSTEM_PROMPT = """
Eres un especialista en normatividad: políticas, manuales, reglas de negocio
y lineamientos internos.

Recibirás una pregunta y la evidencia recuperada del corpus. La evidencia es
la única fuente válida: no uses conocimiento previo ni inventes artículos,
requisitos, plazos o excepciones.

Responde en español, de forma directa, citando textualmente los artículos o
fragmentos que respondan la pregunta y conservando el archivo y la página de
las fuentes. Si la evidencia no alcanza para responder, dilo explícitamente.
No sustituyas la respuesta por una lista de derechos o artículos que no
aparezcan en la evidencia.
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
        self._llm = None
        self._tools = [self._crear_herramienta_busqueda()]
        self.herramientas = [herramienta.name for herramienta in self._tools]

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
                if isinstance(pagina, float) and pagina.is_integer():
                    pagina = int(pagina)

                ubicacion = fuente
                if pagina is not None:
                    ubicacion += f", página {pagina}"
                if fila is not None:
                    ubicacion += f", fila {fila}"

                evidencia.append(f"FUENTE: {ubicacion}\n{doc.page_content}")

            return "\n\n---\n\n".join(evidencia)

        return buscar_normatividad

    def _get_llm(self):
        if self._llm is None:
            self._llm = ChatOllama(model=self.modelo, temperature=0)
        return self._llm

    # Interfaz pública

    def procesar(self, consulta: str) -> str:
        """Recupera evidencia y responde con una síntesis basada en ella."""
        evidencia = self._tools[0].invoke({"query": consulta})
        if not evidencia or evidencia == "No se encontró evidencia normativa relevante.":
            return "No se encontró evidencia normativa relevante para la consulta."

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "EVIDENCIA RECUPERADA (única fuente válida):\n"
            f"{evidencia}\n\n"
            f"PREGUNTA: {consulta}\n\n"
            "Responde citando textualmente los artículos o fragmentos relevantes. "
            "No agregues artículos que no estén en la evidencia."
        )
        respuesta = self._get_llm().invoke(
            [("system", prompt), ("human", consulta)]
        )
        texto = str(respuesta.content).strip()

        fuentes = []
        for fuente in re.findall(r"(?im)^FUENTE:\s*(.+?)\s*$", evidencia):
            if fuente not in fuentes:
                fuentes.append(fuente)
        if fuentes:
            texto += (
                "\n\nFuentes consultadas:\n"
                + "\n".join(f"- {fuente}" for fuente in fuentes)
            )
        return texto

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


normatividad_rag_agent = NormatividadRAGAgent()