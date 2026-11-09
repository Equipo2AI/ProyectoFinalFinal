"""Banco de pruebas manual de document_intelligence.

Uso:
    python tests/probar_document_intelligence.py
    python tests/probar_document_intelligence.py --caso 1
    python tests/probar_document_intelligence.py --imagen ruta/a/factura.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

SEPARADOR = "─" * 78
IMAGEN_POR_DEFECTO = RAIZ / "tests" / "modelo-factura-ex.png"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Banco de pruebas de document_intelligence"
    )
    parser.add_argument(
        "--imagen",
        default=str(IMAGEN_POR_DEFECTO),
        help="Ruta de la imagen a procesar",
    )
    parser.add_argument("--caso", type=int, help="Ejecuta solo el caso N (1-based)")
    args = parser.parse_args()

    ruta = Path(args.imagen).expanduser().resolve()
    if not ruta.is_file():
        parser.error(f"No existe la imagen: {ruta}")

    from proyecto.agents.document_intelligence_agent import (
        DocumentIntelligenceAgent,
        describe_an_image,
        ocr_image,
    )

    agente = DocumentIntelligenceAgent()
    casos = [
        ("OCR · tool directa", lambda: ocr_image.invoke({"image_path": str(ruta)})),
        (
            "Descripción · tool directa",
            lambda: describe_an_image.invoke({"image_path": str(ruta)}),
        ),
        (
            "Agente · OCR",
            lambda: agente.procesar(f"Transcribe todo el texto visible en {ruta}"),
        ),
        (
            "Agente · descripción",
            lambda: agente.procesar(f"Describe la imagen {ruta}"),
        ),
    ]

    seleccionados = list(enumerate(casos, start=1))
    if args.caso:
        if not 1 <= args.caso <= len(casos):
            parser.error(f"--caso debe estar entre 1 y {len(casos)}")
        seleccionados = [(args.caso, casos[args.caso - 1])]

    print(SEPARADOR)
    print(f"document_intelligence · modelo: {agente.modelo}")
    print(f"Imagen: {ruta}")
    print(SEPARADOR)

    for numero, (nombre, ejecutar) in seleccionados:
        print(f"\nCASO {numero} · {nombre}")
        inicio = time.perf_counter()
        resultado = ejecutar()
        print(resultado)
        print(f"\n({time.perf_counter() - inicio:.1f}s)")


if __name__ == "__main__":
    main()
