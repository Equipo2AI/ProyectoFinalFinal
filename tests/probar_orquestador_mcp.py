"""Prueba manual del orquestador con tools MCP.

Uso:
    python tests/probar_orquestador_mcp.py
    python tests/probar_orquestador_mcp.py --consulta "¿Qué problemas de calidad tiene raw_clientes?"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from proyecto.orchestration.orquestador import HERRAMIENTAS_MCP, Orquestador


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consulta", default=None)
    argumentos = parser.parse_args()

    print("Tools MCP:", [herramienta.name for herramienta in HERRAMIENTAS_MCP])

    if not argumentos.consulta:
        return

    orquestador = Orquestador()
    respuesta = orquestador.procesar(argumentos.consulta)
    print("Invocadas:", orquestador.agentes_invocados)
    print("Respuesta:")
    print(respuesta)


if __name__ == "__main__":
    main()
