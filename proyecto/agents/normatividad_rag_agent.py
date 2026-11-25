# Normatividad / RAG Agent — Semantic search in documents.
# Domain: policies, manuals, business rules, semantic memory.

import hashlib
import json
import os
import re
import unicodedata

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_pinecone import PineconeEmbeddings, PineconeVectorStore

from proyecto.config.models import modelo_de

load_dotenv()


def _env_float(nombre: str, defecto: float) -> float:
    try:
        valor = float(os.getenv(nombre, str(defecto)))
    except (TypeError, ValueError):
        return defecto
    return min(max(valor, 0.0), 1.0)


def _env_int(nombre: str, defecto: int) -> int:
    try:
        return max(1, int(os.getenv(nombre, str(defecto))))
    except (TypeError, ValueError):
        return defecto


_PALABRAS_VACIAS = {
    "que", "cual", "cuales", "como", "cuanto", "cuantos", "cuanta",
    "cuantas", "donde", "cuando", "por", "para", "sobre", "entre",
    "segun", "desde", "hasta", "con", "sin", "los", "las", "del",
    "una", "uno", "unos", "unas", "este", "esta", "estos", "estas",
    "ese", "esa", "esos", "esas", "sus", "mi", "mis", "tu", "tus",
    "su", "nuestro", "nuestra", "es", "son", "ser", "estan", "hay",
    "tiene", "tienen", "hace", "hacen", "dice", "decir", "habla",
    "hablar", "trata", "tratar", "tema", "al", "el", "la", "lo",
    "establece", "establecen", "establecer", "disposicion",
    "disposiciones", "contiene", "contienen", "indica", "indican",
    "senala", "senalar", "regula", "regulan", "aplica", "aplican",
    "dispone", "disponen", "define", "definen", "menciona",
    "mencionan",
}

# Términos que no sirven para detectar un tema ausente: aparecen en casi
# todo el corpus normativo y no distinguen una consulta de otra.
_TERMINOS_GENERICOS = {
    "ley", "norma", "normativa", "documento", "persona", "defensa",
    "usuario", "servicio", "financiero", "tratamiento", "dice",
    "decir", "habla", "hablar", "trata", "tratar", "tema",
    "funciona", "funcionar", "requisito", "plazo", "condicion",
    "concepto", "significa", "significado",
}

def _normalizar_texto(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "").casefold()
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _singular(palabra: str) -> str:
    if len(palabra) > 4 and palabra.endswith("es"):
        return palabra[:-2]
    if len(palabra) > 3 and palabra.endswith("s"):
        return palabra[:-1]
    return palabra


def _terminos(texto: str) -> set[str]:
    palabras = re.findall(r"[a-z0-9]{3,}", _normalizar_texto(texto))
    return {
        _singular(palabra)
        for palabra in palabras
        if palabra not in _PALABRAS_VACIAS
    }


def _contiene(terminos_texto: set[str], termino: str) -> bool:
    """Coincidencia exacta o por raíz morfológica compartida.

    La raíz cubre variantes como "presentar"/"presentarse" o
    "documento"/"documentación" sin mantener un diccionario de sinónimos.
    """
    if termino in terminos_texto:
        return True
    if len(termino) < 6:
        return False
    raiz = termino[:6]
    return any(candidato.startswith(raiz) for candidato in terminos_texto)


def _entidades_consulta(texto: str) -> set[str]:
    """Nombres propios, siglas y cifras que deben existir en el corpus.

    No intenta identificar verbos de intención como "necesito", "mover" o
    "estoy": esos los resuelve el score semántico. Solo marca señales duras
    (por ejemplo "Japón", "AFORE" o una cifra) que, si faltan, indican que la
    consulta probablemente está fuera del corpus.
    """
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", texto or "")
    entidades: set[str] = set()
    for token in tokens:
        if not any(caracter.isalpha() for caracter in token):
            if len(token) >= 2 and token.isdigit():
                entidades.add(token)
            continue
        if token.isupper() and len(token) >= 2:
            entidades.add(_singular(_normalizar_texto(token)))
            continue
        normalizado = _normalizar_texto(token)
        if token[0].isupper() and normalizado not in _PALABRAS_VACIAS:
            entidades.add(_singular(normalizado))
    return entidades


def _cobertura(terminos_consulta: set[str], terminos_texto: set[str]) -> float:
    if not terminos_consulta:
        return 0.0
    encontrados = sum(
        1 for termino in terminos_consulta if _contiene(terminos_texto, termino)
    )
    return encontrados / len(terminos_consulta)


def _coincidencia_fuente(terminos_consulta: set[str], fuente: str) -> float:
    return _cobertura(terminos_consulta, _terminos(fuente))


class NormatividadRAGAgent:
    def __init__(self, k: int = 3):
        self.nombre = "normatividad_rag"
        self.descripcion = "Búsqueda semántica sobre documentación normativa (RAG)."
        self.modelo = modelo_de("normatividad_rag")
        self.modelo_embeddings = "llama-text-embed-v2"  # debe coincidir con el usado al indexar
        self.k = k
        self.candidate_k = max(self.k, _env_int("RAG_CANDIDATES", 12))
        self.min_score = _env_float("RAG_MIN_SCORE", 0.25)
        self.strong_score = _env_float("RAG_STRONG_SCORE", 0.70)
        self.min_coverage = _env_float("RAG_MIN_COVERAGE", 0.35)
        self.min_source_match = _env_float("RAG_MIN_SOURCE_MATCH", 0.50)

        # Los componentes pesados se crean al primer uso, para que importar
        # este módulo dentro de la arquitectura mayor no requiera Pinecone/Ollama.
        self._retriever = None
        self._vector_store = None
        self._cache_evidencia: dict[str, dict] = {}
        # Una sola recuperación por consulta: buscar_normatividad,
        # buscar_evidencia y verificar_cita comparten estos fragmentos.
        self._cache_consultas: dict[str, list[str]] = {}
        self._cache_diagnosticos: dict[str, dict] = {}
        self._tools = list(self._crear_herramientas_busqueda())
        self.herramientas = [herramienta.name for herramienta in self._tools]

    def _get_retriever(self):
        if self._retriever is None:
            # Embeddings hospedados por Pinecone (usa PINECONE_API_KEY del entorno).
            vector_store = PineconeVectorStore(
                index_name=os.environ["INDEX_NAME"],
                embedding=PineconeEmbeddings(model=self.modelo_embeddings),
            )
            self._vector_store = vector_store
            self._retriever = vector_store.as_retriever(
                search_kwargs={"k": self.candidate_k},
            )
        return self._retriever

    def _buscar_con_score(self, query: str):
        """Devuelve documentos y score semántico; tolera retrievers sin score."""
        if self._vector_store is None:
            self._get_retriever()
        if self._vector_store is not None:
            return self._vector_store.similarity_search_with_score(
                query, k=self.candidate_k
            )
        retriever = self._retriever
        if hasattr(retriever, "similarity_search_with_score"):
            return retriever.similarity_search_with_score(
                query, k=self.candidate_k
            )
        documentos = retriever.invoke(query) if retriever is not None else []
        return [(documento, None) for documento in documentos or []]

    def _fragmento(
        self,
        doc,
        indice: int,
        score=None,
        metricas: dict | None = None,
    ) -> dict:
        metricas = metricas or {}
        metadata = doc.metadata or {}
        fuente = metadata.get("source", "desconocida")
        pagina = metadata.get("page")
        fila = metadata.get("row")
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
            "score_semantico": (
                round(float(score), 4) if score is not None else None
            ),
            "score_hibrido": round(float(metricas.get("score_hibrido", 0.0)), 4),
            "cobertura": round(float(metricas.get("cobertura", 0.0)), 4),
            "coincidencia_fuente": round(
                float(metricas.get("coincidencia_fuente", 0.0)), 4
            ),
            "cobertura_distintiva": round(
                float(metricas.get("cobertura_distintiva", 0.0)), 4
            ),
            "coincidencia_fuente_distintiva": round(
                float(metricas.get("coincidencia_fuente_distintiva", 0.0)), 4
            ),
            "origen": metricas.get(
                "origen", "pinecone" if score is not None else "local"
            ),
        }
        self._cache_evidencia[cita_id] = fragmento
        return fragmento

    def _puntuar(self, terminos_consulta: set[str], doc, score, origen: str) -> dict:
        metadata = doc.metadata or {}
        texto = str(doc.page_content or "")
        fuente = str(metadata.get("source", "desconocida"))
        terminos_texto = _terminos(texto)
        terminos_fuente = _terminos(fuente)
        cobertura = _cobertura(terminos_consulta, terminos_texto)
        coincidencia = _cobertura(terminos_consulta, terminos_fuente)
        distintivos = terminos_consulta - _TERMINOS_GENERICOS
        cobertura_distintiva = _cobertura(distintivos, terminos_texto)
        fuente_distintiva = _cobertura(distintivos, terminos_fuente)
        semantico = float(score) if score is not None else None
        base = semantico if semantico is not None else 0.0
        return {
            "doc": doc,
            "score_semantico": semantico,
            "cobertura": cobertura,
            "coincidencia_fuente": coincidencia,
            "cobertura_distintiva": cobertura_distintiva,
            "coincidencia_fuente_distintiva": fuente_distintiva,
            # El score semántico manda; la cobertura y la fuente desempatan y
            # corrigen casos como "crédito hipotecario" o "afore".
            "score_hibrido": base + 0.05 * cobertura + 0.15 * coincidencia,
            "origen": origen,
        }

    def _filtrar_candidatos(
        self,
        query: str,
        resultados: list,
        origen: str = "pinecone",
    ) -> tuple[list[dict], dict]:
        terminos_consulta = _terminos(query)
        candidatos = [
            self._puntuar(terminos_consulta, doc, score, origen)
            for doc, score in resultados or []
        ]

        terminos_corpus: set[str] = set()
        for candidato in candidatos:
            metadata = candidato["doc"].metadata or {}
            terminos_corpus |= _terminos(candidato["doc"].page_content or "")
            terminos_corpus |= _terminos(metadata.get("source", ""))

        distintivos = terminos_consulta - _TERMINOS_GENERICOS
        ausentes = sorted(
            termino
            for termino in distintivos
            if not _contiene(terminos_corpus, termino)
        )
        entidades = _entidades_consulta(query)
        entidades_ausentes = sorted(
            entidad
            for entidad in entidades
            if not _contiene(terminos_corpus, entidad)
        )

        # Las palabras de intención ("necesito", "mover", "estoy") no deben
        # bloquear una consulta: el score semántico ya las pondera. Si faltan,
        # solo restan un poco al score híbrido; no se usa una lista manual de
        # sinónimos ni se exige que todas aparezcan en el corpus.
        if distintivos:
            proporcion_ausente = len(ausentes) / len(distintivos)
            for candidato in candidatos:
                candidato["score_hibrido"] = max(
                    0.0,
                    candidato["score_hibrido"] - 0.10 * proporcion_ausente,
                )

        aceptados: list[dict] = []
        for candidato in candidatos:
            score = candidato["score_semantico"]
            cobertura = candidato["cobertura"]
            fuente = candidato["coincidencia_fuente"]

            # Una entidad dura ausente (nombre propio, sigla o cifra) sí es
            # señal de consulta fuera del corpus. Las palabras de intención no
            # entran aquí porque no se detectan como entidades.
            if entidades_ausentes and (score is None or score < self.strong_score):
                continue

            # Un fragmento que solo comparte términos genéricos ("plazo",
            # "ley", "usuario") no responde a la parte distintiva de la
            # consulta. El umbral es bajo para no castigar la intención.
            if distintivos and max(
                candidato["cobertura_distintiva"],
                candidato["coincidencia_fuente_distintiva"],
            ) < 0.34:
                continue

            if score is None:
                # En el fallback local no hay score semántico. Exigir
                # coincidencia de fuente evita aceptar pasajes que solo
                # comparten palabras sueltas (por ejemplo, "capital" y
                # "Francia" en un documento financiero). Un retriever
                # inyectado sin scores (pruebas/legacy) se acepta por
                # cobertura léxica para no romper su contrato.
                if origen == "local":
                    if (
                        fuente < self.min_source_match
                        or cobertura < self.min_coverage
                    ):
                        continue
                elif not (
                    cobertura >= self.min_coverage
                    or fuente >= self.min_source_match
                ):
                    continue
            else:
                if score < self.min_score:
                    # Los scores bajos se aceptan si la fuente o el texto
                    # coinciden de forma clara con la consulta.
                    if not (
                        fuente >= max(self.min_source_match, 0.75)
                        and cobertura >= max(self.min_coverage, 0.50)
                    ):
                        continue
                if not (
                    cobertura >= self.min_coverage
                    or fuente >= self.min_source_match
                    or score >= self.strong_score
                ):
                    continue

            aceptados.append(candidato)

        aceptados.sort(
            key=lambda candidato: (
                candidato["score_hibrido"],
                candidato["score_semantico"] or 0.0,
            ),
            reverse=True,
        )

        fragmentos: list[dict] = []
        vistos: set[tuple] = set()
        for candidato in aceptados:
            doc = candidato["doc"]
            metadata = doc.metadata or {}
            texto = str(doc.page_content or "")
            clave = (
                metadata.get("source", "desconocida"),
                metadata.get("page"),
                metadata.get("row"),
                _normalizar_texto(texto)[:300],
            )
            if clave in vistos:
                continue
            vistos.add(clave)
            fragmentos.append(
                self._fragmento(
                    doc,
                    len(fragmentos),
                    candidato["score_semantico"],
                    candidato,
                )
            )
            if len(fragmentos) >= self.k:
                break

        motivo = "ok"
        if not candidatos:
            motivo = "sin_candidatos"
        elif not fragmentos:
            motivo = (
                "entidad_ausente"
                if entidades_ausentes
                else "bajo_umbral"
            )

        diagnostico = {
            "candidatos": len(candidatos),
            "aceptados": len(fragmentos),
            "descartados": len(candidatos) - len(fragmentos),
            "ausentes": ausentes,
            "entidades_ausentes": entidades_ausentes,
            "motivo": motivo,
            "umbrales": {
                "min_score": self.min_score,
                "strong_score": self.strong_score,
                "min_coverage": self.min_coverage,
                "min_source_match": self.min_source_match,
            },
        }
        return fragmentos, diagnostico

    def _recuperar(self, query: str) -> list[dict]:
        clave = (query or "").strip()
        if clave in self._cache_consultas:
            return [
                self._cache_evidencia[cita_id]
                for cita_id in self._cache_consultas[clave]
            ]

        error = None
        try:
            resultados = self._buscar_con_score(clave)
        except Exception as exc:  # noqa: BLE001 - la recuperación remota es best-effort
            resultados = []
            error = type(exc).__name__

        fragmentos, diagnostico = self._filtrar_candidatos(
            clave, resultados, "pinecone"
        )
        if error:
            diagnostico["error"] = error
        self._cache_consultas[clave] = [
            fragmento["cita_id"] for fragmento in fragmentos
        ]
        self._cache_diagnosticos[clave] = diagnostico
        return fragmentos

    def _recuperar_local(self, query: str) -> list[dict]:
        """Fallback lexical con el mismo filtro híbrido, sin red."""
        clave = (query or "").strip()
        if clave in self._cache_consultas:
            return [
                self._cache_evidencia[cita_id]
                for cita_id in self._cache_consultas[clave]
            ]

        error = None
        try:
            from proyecto.agents.local_documents import retrieve

            documentos = retrieve(query, self.candidate_k)
        except Exception as exc:  # noqa: BLE001 - el corpus local es best-effort
            documentos = []
            error = type(exc).__name__

        resultados = [
            (
                Document(
                    page_content=documento.get("text", ""),
                    metadata={
                        "source": documento.get("source", "desconocida"),
                        "page": documento.get("page"),
                    },
                ),
                None,
            )
            for documento in documentos or []
        ]
        fragmentos, diagnostico = self._filtrar_candidatos(
            clave, resultados, "local"
        )
        if error:
            diagnostico["error"] = error
        self._cache_consultas[clave] = [
            fragmento["cita_id"] for fragmento in fragmentos
        ]
        self._cache_diagnosticos[clave] = diagnostico
        return fragmentos

    @staticmethod
    def _etiqueta_score(fragmento: dict) -> str:
        partes = []
        if fragmento.get("score_hibrido") is not None:
            partes.append(f"híbrido {fragmento['score_hibrido']:.4f}")
        if fragmento.get("score_semantico") is not None:
            partes.append(f"semántico {fragmento['score_semantico']:.4f}")
        if not partes:
            return ""
        return " (score " + ", ".join(partes) + ")"

    def _formatear_fragmentos(self, fragmentos: list[dict]) -> str:
        return "\n\n---\n\n".join(
            f"FUENTE: {self._ubicacion(fragmento)}"
            f"{self._etiqueta_score(fragmento)}\n{fragmento['texto']}"
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

    def _mensaje_sin_evidencia(self, query: str) -> str:
        diagnostico = self._cache_diagnosticos.get((query or "").strip(), {})
        entidades = diagnostico.get("entidades_ausentes") or []
        detalle = ""
        if entidades:
            detalle = (
                " No encontré evidencia sobre: "
                + ", ".join(entidades)
                + "."
            )
        elif diagnostico.get("motivo") == "bajo_umbral":
            detalle = " Los fragmentos recuperados no fueron suficientemente relevantes."
        elif diagnostico.get("error"):
            detalle = f" Error de recuperación: {diagnostico['error']}."
        return (
            "Sin evidencia documental suficiente: no se encontraron fragmentos "
            "relevantes para responder sin inventar." + detalle
        )

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
                return self._mensaje_sin_evidencia(query)
            return self._formatear_fragmentos(fragmentos)

        @tool
        def buscar_evidencia(query: str) -> str:
            """Recupera fragmentos crudos con fuente, página/fila, score y cita_id."""
            fragmentos = self._recuperar(query)
            clave = (query or "").strip()
            diagnostico = self._cache_diagnosticos.get(clave, {})
            return json.dumps(
                {
                    "consulta": query,
                    "total": len(fragmentos),
                    "sin_evidencia": not fragmentos,
                    "fragmentos": fragmentos,
                    "diagnostico": diagnostico,
                    "nota": (
                        "La evidencia es la única fuente válida; usa cita_id y "
                        "verificar_cita antes de afirmar una cita textual. "
                        "Si sin_evidencia es true, no inventes una respuesta."
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

    # Interfaz pública

    def procesar(self, consulta: str) -> str:
        """Recupera evidencia y la devuelve con fuentes, sin inventar una síntesis."""
        from queue import Queue, Empty
        from threading import Thread
        import socket

        result = Queue()

        def remote():
            try:
                with socket.create_connection(("api.pinecone.io", 443), timeout=3):
                    pass
                result.put(("pinecone", self._tools[0].invoke({"query": consulta})))
            except Exception as error:
                result.put(("error", type(error).__name__))

        Thread(target=remote, daemon=True).start()
        try:
            mode, evidence = result.get(timeout=12)
        except Empty:
            mode, evidence = "error", "Tiempo de recuperacion remota agotado"

        if (
            mode == "pinecone"
            and evidence
            and not evidence.startswith(("No se encontr", "Sin evidencia"))
        ):
            return "## Evidencia normativa recuperada\n\n" + evidence

        # Si Pinecone no dio evidencia, se intenta el corpus local con el
        # mismo filtro híbrido; así una caída remota no inventa una respuesta.
        clave = (consulta or "").strip()
        self._cache_consultas.pop(clave, None)
        self._cache_diagnosticos.pop(clave, None)
        fragmentos = self._recuperar_local(consulta)
        if not fragmentos:
            return self._mensaje_sin_evidencia(consulta)
        return (
            "## Evidencia normativa recuperada\n\n"
            "Respuesta basada en fragmentos literales del corpus local "
            "(Pinecone no disponible o sin evidencia suficiente).\n\n"
            + self._formatear_fragmentos(fragmentos)
        )

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


normatividad_rag_agent = NormatividadRAGAgent()
