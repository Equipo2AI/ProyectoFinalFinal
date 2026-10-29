"""Document Intelligence Agent — OCR and image understanding.

Dominio: lectura de imágenes (facturas, recibos, comprobantes) mediante
Qwen2.5-VL servido localmente con Ollama. Si existe `FASTOCR_API_KEY` y el
paquete `fastocr-sdk`, `ocr_image` usa FastOCR como vía rápida. No hace OCR de
PDFs; para documentos normativos en PDF existe el agente `normatividad_rag`.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

MODELO_VISION_POR_DEFECTO = os.getenv("DOCUMENT_INTELLIGENCE_MODEL", "qwen2.5vl:7b")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PATRON_RUTA_IMAGEN = re.compile(
    r"""["']([^"']+\.(?:png|jpe?g|webp|gif|bmp))["']|(\S+\.(?:png|jpe?g|webp|gif|bmp))""",
    re.IGNORECASE,
)
PALABRAS_DESCRIPCION = (
    "describe",
    "descripción",
    "descripcion",
    "qué ves",
    "que ves",
    "interpreta",
    "resume",
    "resumen",
    "analiza",
)

PROMPT_OCR = """
Transcribe literalmente todo el texto visible en la imagen.
Conserva el orden de lectura, los saltos de línea y la estructura de tablas.
No agregues comentarios, interpretaciones ni datos que no aparezcan.
Si no hay texto legible, responde exactamente: "No se detectó texto legible."
"""

PROMPT_DESCRIPCION = """
Describe la imagen con detalle.
Si parece una factura, recibo o comprobante, indica tipo de documento, emisor,
folio, fecha, conceptos, montos y moneda solo cuando sean visibles.
Si hay texto, transcríbelo de forma resumida. No inventes datos faltantes.
"""


def _resolver_imagen(ruta: str) -> Path:
    """Validate an image path and return it resolved."""
    if not ruta or not str(ruta).strip():
        raise ValueError("Falta la ruta de la imagen.")

    path = Path(ruta).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la imagen: {ruta}")
    if path.suffix.lower() not in EXTENSIONES_IMAGEN:
        permitidas = ", ".join(sorted(EXTENSIONES_IMAGEN))
        raise ValueError(f"Formato no soportado: {path.suffix}. Usa: {permitidas}.")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("La imagen supera el límite de 10 MB.")
    return path


def _imagen_data_url(path: Path) -> str:
    """Encode a local image as a data URL understood by ChatOllama."""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    contenido = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{contenido}"


def _extraer_texto(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        bloque.get("text", "")
        for bloque in content
        if isinstance(bloque, dict) and bloque.get("type") == "text"
    )


def _vision(prompt: str, ruta: str, modelo: str) -> str:
    """Send one prompt plus one local image to Qwen2.5-VL through Ollama."""
    path = _resolver_imagen(ruta)
    llm = ChatOllama(model=modelo, temperature=0)
    # La imagen va primero para que Ollama pueda reutilizar su prefijo cacheado
    # cuando se hacen varias preguntas sobre la misma imagen.
    mensaje = HumanMessage(
        content=[
            {"type": "image_url", "image_url": {"url": _imagen_data_url(path)}},
            {"type": "text", "text": prompt},
        ]
    )
    respuesta = llm.invoke([mensaje])
    return _extraer_texto(respuesta.content).strip()


def _error_legible(error: Exception) -> str:
    return f"No se pudo procesar la imagen: {type(error).__name__}: {error}"


def _extraer_ruta_imagen(consulta: str) -> str | None:
    """Find an image path inside a free-form query."""
    coincidencia = PATRON_RUTA_IMAGEN.search(consulta)
    if not coincidencia:
        return None
    return coincidencia.group(1) or coincidencia.group(2)


def _quiere_descripcion(consulta: str) -> bool:
    texto = consulta.lower()
    return any(palabra in texto for palabra in PALABRAS_DESCRIPCION)


def _ocr_con_fastocr(path: Path) -> str | None:
    """Use FastOCR when FASTOCR_API_KEY and fastocr-sdk are available."""
    if not os.getenv("FASTOCR_API_KEY"):
        return None
    try:
        from fastocr_sdk import FastOCR
    except ImportError:
        return None

    with FastOCR() as client:
        texto = client.extract_text(str(path))
    return str(texto).strip() or None


@tool
def ocr_image(image_path: str) -> str:
    """Extrae el texto visible de una imagen local (factura, recibo, comprobante)."""
    try:
        path = _resolver_imagen(image_path)
        texto = _ocr_con_fastocr(path)
        if texto:
            return texto
        return _vision(PROMPT_OCR, image_path, MODELO_VISION_POR_DEFECTO)
    except Exception as error:  # noqa: BLE001 - se devuelve al agente como texto
        return _error_legible(error)


@tool
def describe_an_image(image_path: str) -> str:
    """Describe una imagen local y, si es un comprobante, resume sus datos visibles."""
    try:
        return _vision(PROMPT_DESCRIPCION, image_path, MODELO_VISION_POR_DEFECTO)
    except Exception as error:  # noqa: BLE001 - se devuelve al agente como texto
        return _error_legible(error)


class DocumentIntelligenceAgent:
    def __init__(self, modelo: str | None = None):
        self.nombre = "document_intelligence"
        self.descripcion = (
            "Lee imágenes (facturas, recibos, comprobantes) con OCR y visión. "
            "Requiere la ruta local de la imagen."
        )
        # Qwen2.5-VL no soporta tool calling en Ollama. En vez de cargar un
        # segundo LLM solo para enrutar, `procesar` despacha a la tool adecuada.
        self.modelo = modelo or MODELO_VISION_POR_DEFECTO
        self._tools = [ocr_image, describe_an_image]
        self.herramientas = [herramienta.name for herramienta in self._tools]

    def procesar(self, consulta: str) -> str:
        ruta = _extraer_ruta_imagen(consulta)
        if not ruta:
            return (
                "Proporciona la ruta local de una imagen "
                "(por ejemplo: tests/modelo-factura-ex.png)."
            )
        if _quiere_descripcion(consulta):
            return describe_an_image.invoke({"image_path": ruta})
        return ocr_image.invoke({"image_path": ruta})

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | visión: {self.modelo}>"


document_intelligence_agent = DocumentIntelligenceAgent()
