# Normatividad / RAG Agent — Semantic search in documents.
# Domain: policies, manuals, business rules, semantic memory.

import hashlib
import json
import os
import re
import unicodedata

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore

from proyecto.config.models import modelo_de

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
        self.modelo = modelo_de("normatividad_rag")
        self.modelo_embeddings = "llama-text-embed-v2"  # debe coincidir con el usado al indexar
        self.k = k

        # Los componentes pesados se crean al primer uso, para que importar
        # este módulo dentro de la arquitectura mayor no requiera Pinecone/Ollama.
        self._retriever = None
        self._llm = None
        self._cache_evidencia: dict[str, dict] = {}
        # Una sola recuperación por consulta: buscar_normatividad,
        # buscar_evidencia y verificar_cita comparten estos fragmentos.
        self._cache_consultas: dict[str, list[str]] = {}
        self._tools = list(self._crear_herramientas_busqueda())
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

    def _fragmento(self, doc, indice: int) -> dict:
        fuente = doc.metadata.get("source", "desconocida")
        pagina = doc.metadata.get("page")
        fila = doc.metadata.get("row")
        if isinstance(pagina, float) and pagina.is_integer():
            pagina = int(pagina)
        texto = str(doc.page_content or "")
        semilla = f"{fuente}|{pagina}|{fila}|{indice}|{texto[:200]}"
        cita_id = hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:16]
        fragmento = {
            "cita_id": cita_id,
            "fuente": fuente,
            "pagina": pagina,
            "fila": fila,
            "texto": texto,
        }
        self._cache_evidencia[cita_id] = fragmento
        return fragmento

    def _recuperar(self, query: str) -> list[dict]:
        clave = (query or "").strip()
        if clave in self._cache_consultas:
            return [
                self._cache_evidencia[cita_id]
                for cita_id in self._cache_consultas[clave]
            ]
        docs = self._get_retriever().invoke(query)
        fragmentos = [
            self._fragmento(doc, indice)
            for indice, doc in enumerate(docs or [])
        ]
        self._cache_consultas[clave] = [
            fragmento["cita_id"] for fragmento in fragmentos
        ]
        return fragmentos

    def _formatear_fragmentos(self, fragmentos: list[dict]) -> str:
        return "\n\n---\n\n".join(
            f"FUENTE: {self._ubicacion(fragmento)}\n{fragmento['texto']}"
            for fragmento in fragmentos
        )

    @staticmethod
    def _ubicacion(fragmento: dict) -> str:
        ubicacion = str(fragmento.get("fuente") or "desconocida")
        pagina = fragmento.get("pagina")
        fila = fragmento.get("fila")
        if pagina is not None:
            ubicacion += f", página {pagina}"
        if fila is not None:
            ubicacion += f", fila {fila}"
        return ubicacion

    @staticmethod
    def _normalizar_cita(texto: str) -> str:
        normalizado = unicodedata.normalize("NFKC", texto or "").casefold()
        return re.sub(r"\s+", " ", normalizado).strip()

    def _crear_herramientas_busqueda(self):
        @tool
        def buscar_normatividad(query: str) -> str:
            """Busca y devuelve evidencia normativa formateada; conserva compatibilidad."""
            fragmentos = self._recuperar(query)
            if not fragmentos:
                return "No se encontró evidencia normativa relevante."
            return self._formatear_fragmentos(fragmentos)

        @tool
        def buscar_evidencia(query: str) -> str:
            """Recupera fragmentos crudos con fuente, página/fila y cita_id, sin sintetizar."""
            fragmentos = self._recuperar(query)
            return json.dumps(
                {
                    "consulta": query,
                    "total": len(fragmentos),
                    "fragmentos": fragmentos,
                    "nota": (
                        "La evidencia es la única fuente válida; usa cita_id y "
                        "verificar_cita antes de afirmar una cita textual."
                    ),
                },
                ensure_ascii=False,
                default=str,
            )

        @tool
        def verificar_cita(cita: str, query: str = "", cita_id: str = "") -> str:
            """Verifica que una cita textual aparezca en la evidencia recuperada.

            Args:
                cita: Texto que se quiere verificar.
                query: Consulta original para recuperar la misma evidencia.
                cita_id: Identificador devuelto por buscar_evidencia.
            """
            consulta_usada = query or "(cache)"
            if cita_id:
                # La verificación no vuelve a consultar el índice: solo puede
                # validar evidencia que ya fue recuperada en esta sesión.
                fragmento = self._cache_evidencia.get(cita_id)
                if fragmento is None:
                    return json.dumps(
                        {
                            "existe": False,
                            "coincide": False,
                            "consulta_usada": consulta_usada,
                            "cita_id": cita_id,
                            "fragmentos_revisados": 0,
                            "error": "cita_id_no_en_cache",
                            "nota": (
                                "El cita_id no corresponde a evidencia recuperada "
                                "en esta sesión. Usa buscar_evidencia y verifica "
                                "contra el cita_id devuelto."
                            ),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                fragmentos = [fragmento]
            elif query and query.strip() in self._cache_consultas:
                # Reutiliza exactamente la recuperación ya hecha por
                # buscar_normatividad o buscar_evidencia.
                fragmentos = [
                    self._cache_evidencia[cita]
                    for cita in self._cache_consultas[query.strip()]
                ]
            elif query:
                fragmentos = self._recuperar(query)
            else:
                fragmentos = []

            buscada = self._normalizar_cita(cita)
            for fragmento in fragmentos:
                if buscada and buscada in self._normalizar_cita(fragmento["texto"]):
                    return json.dumps(
                        {
                            "existe": True,
                            "coincide": True,
                            "consulta_usada": consulta_usada,
                            **fragmento,
                        },
                        ensure_ascii=False,
                        default=str,
                    )

            return json.dumps(
                {
                    "existe": False,
                    "coincide": False,
                    "consulta_usada": consulta_usada,
                    "cita": cita,
                    "fragmentos_revisados": len(fragmentos),
                    "nota": (
                        "La cita no aparece literalmente en la evidencia "
                        "recuperada. Reformula la búsqueda o no la presentes "
                        "como cita textual."
                    ),
                },
                ensure_ascii=False,
                default=str,
            )

        return buscar_normatividad, buscar_evidencia, verificar_cita

    def _get_llm(self):
        if self._llm is None:
            self._llm = ChatOllama(model=self.modelo, temperature=0)
        return self._llm

    # Interfaz pública

    def procesar(self, consulta: str) -> str:
        """Recupera evidencia y la devuelve con fuentes, sin inventar una síntesis."""
        from queue import Queue, Empty
        from threading import Thread
        import socket
        from proyecto.agents.local_documents import retrieve
        result = Queue()
        def remote():
            try:
                with socket.create_connection(('api.pinecone.io',443),timeout=3):
                    pass
                result.put(('pinecone', self._tools[0].invoke({'query': consulta})))
            except Exception as error:
                result.put(('error', type(error).__name__))
        Thread(target=remote,daemon=True).start()
        try:
            mode, evidence = result.get(timeout=12)
        except Empty:
            mode, evidence = 'error', 'Tiempo de recuperacion remota agotado'
        if mode == 'pinecone' and evidence and not evidence.startswith('No se encontr'):
            return 'Evidencia documental recuperada mediante Pinecone:\n\n' + evidence
        docs = retrieve(consulta,self.k)
        if not docs:
            return 'No se encontro evidencia documental suficiente. Recuperacion remota no disponible; no se inventa una respuesta.'
        answer = 'Respuesta basada en fragmentos literales del corpus local (Pinecone no disponible).\n\n'
        for doc in docs:
            answer += f"FUENTE: {doc['source']}, pagina {doc['page']}\n\n> {doc['text']}\n\n"
        return answer.strip()

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


normatividad_rag_agent = NormatividadRAGAgent()