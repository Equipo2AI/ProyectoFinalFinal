"""Banco de pruebas del agente Normatividad / RAG.

Uso:
    python probar_normatividad_rag.py                 # todos los casos
    python probar_normatividad_rag.py --caso 4        # solo el caso 4
    python probar_normatividad_rag.py --sin-evidencia # solo respuesta del agente
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# Ejecutable desde la raíz del proyecto o desde una subcarpeta (p. ej. tests/).
AQUI = Path(__file__).resolve().parent
for candidato in (AQUI, AQUI.parent):
    if str(candidato) not in sys.path:
        sys.path.insert(0, str(candidato))
    load_dotenv(candidato / ".env")

SEPARADOR = "─" * 78
MAX_EVIDENCIA = 700

# Rutas posibles del módulo del agente, en orden de prueba.
MODULOS_AGENTE = (
    "proyecto.agents.normatividad_rag_agent",
    "agents.normatividad_rag_agent",
    "normatividad_rag_agent",
)

# (categoría, consulta)
CASOS = [
    # --- preguntas que el corpus debería cubrir ---
    (
        "En dominio · privacidad",
        "¿Qué obligaciones establece el aviso de privacidad de Banamex sobre el "
        "tratamiento de datos personales?",
    ),
    (
        "En dominio · conservación",
        "¿Qué dice la Ley Federal de Protección de Datos Personales en Posesión de "
        "los Particulares sobre la conservación de los datos?",
    ),
    (
        "En dominio · contratos",
        "¿Qué establecen los términos y condiciones de Citibanamex sobre el uso de la "
        "información del cliente?",
    ),
    # --- preguntas sin relación con el corpus ---
    (
        "Fuera de dominio",
        "¿Quién ganó el mundial de fútbol de 2022 y cuál fue el marcador de la final?",
    ),
    (
        "Fuera de dominio · dato inexistente",
        "¿Cuál es la tasa de interés hipotecaria de Banamex para diciembre de 2026?",
    ),
    (
        "Fuera de dominio · dato de cliente",
        "¿Cuál es el saldo de la cuenta 100123 del cliente 42?",
    ),
    # --- ambigüedad: la pregunta no permite saber qué evidencia aplicar ---
    ("Ambigüedad · sujeto", "¿Cuál es el plazo?"),
    ("Ambigüedad · norma", "¿Qué dice la ley?"),
    (
        "Ambigüedad · alcance",
        "¿Se puede compartir la información del cliente con terceros?",
    ),
    ("Ambigüedad · referencia", "¿Y si el cliente es menor de edad?"),
    (
        "Ambigüedad · entidad",
        "¿Esto aplica para Banamex o solo para Citibanamex?",
    ),
    ("Ambigüedad · consecuencia", "¿Qué pasa si no cumplo?"),
    ("Ambigüedad · cuantía", "¿De cuánto es la sanción?"),
    ("Ambigüedad · vigencia", "¿Sigue vigente?"),
]


def _acota(texto: str, limite: int = MAX_EVIDENCIA) -> str:
    texto = str(texto).strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite] + " […]"


def _cargar_agente():
    """Importa el agente probando las rutas conocidas y muestra el error real."""
    errores = []
    for nombre in MODULOS_AGENTE:
        try:
            modulo = importlib.import_module(nombre)
            return modulo.normatividad_rag_agent
        except ModuleNotFoundError as e:
            # Solo se salta si falta el módulo del agente; una dependencia
            # faltante (langchain, pinecone...) se reporta tal cual.
            if e.name and nombre.startswith(e.name):
                errores.append(f"  - {nombre}: no existe")
                continue
            raise
    sys.exit(
        "No se encontró el módulo del agente. Probé:\n"
        + "\n".join(errores)
        + "\nAjusta MODULOS_AGENTE al paquete real."
    )


def _preflight() -> list[str]:
    """Revisa lo que el RAG necesita antes de gastar tiempo en los casos."""
    problemas = []
    for var in ("INDEX_NAME", "PINECONE_API_KEY"):
        if not os.environ.get(var):
            problemas.append(f"Falta la variable de entorno {var} (revisa tu .env)")

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = f"http://{host}"
    try:
        urllib.request.urlopen(host, timeout=3)
    except Exception as e:  # noqa: BLE001
        problemas.append(f"Ollama no responde en {host} ({type(e).__name__}). ¿`ollama serve`?")
    return problemas


def main() -> None:
    parser = argparse.ArgumentParser(description="Banco de pruebas del RAG")
    parser.add_argument("--caso", type=int, help="Ejecuta solo el caso N (1-based)")
    parser.add_argument(
        "--sin-evidencia",
        action="store_true",
        help="No imprime la evidencia recuperada, solo la respuesta del agente",
    )
    args = parser.parse_args()

    casos = list(enumerate(CASOS, start=1))
    if args.caso:
        if not 1 <= args.caso <= len(CASOS):
            parser.error(f"--caso debe estar entre 1 y {len(CASOS)}")
        casos = [(args.caso, CASOS[args.caso - 1])]

    agente = _cargar_agente()
    herramienta = agente.herramientas[0]

    print(SEPARADOR)
    print(f"Normatividad / RAG · modelo: {agente.modelo}")
    print(
        f"Embeddings: {agente.modelo_embeddings} · "
        f"índice: {os.environ.get('INDEX_NAME')} · k = {agente.k}"
    )
    for problema in _preflight():
        print(f"AVISO: {problema}")
    print(SEPARADOR)

    for numero, (categoria, consulta) in casos:
        print(f"\nCASO {numero} · {categoria}")
        print(f"CONSULTA: {consulta}")

        if not args.sin_evidencia:
            inicio = time.perf_counter()
            try:
                # La herramienta recibe un único argumento `query`.
                evidencia = herramienta.invoke({"query": consulta})
            except Exception as error:  # noqa: BLE001
                evidencia = f"[ERROR al recuperar] {type(error).__name__}: {error}"
            print(f"EVIDENCIA ({time.perf_counter() - inicio:.1f}s):")
            print(_acota(evidencia))

        inicio = time.perf_counter()
        try:
            respuesta = agente.procesar(consulta)
        except Exception as error:  # noqa: BLE001
            respuesta = f"[ERROR] {type(error).__name__}: {error}"

        print(f"RESPUESTA ({time.perf_counter() - inicio:.1f}s):")
        print(str(respuesta).strip())
        print(SEPARADOR)


if __name__ == "__main__":
    main()