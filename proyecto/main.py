"""Entry point for listing agents or asking the Data Steward a question."""

import argparse
import sys
from pathlib import Path

# Permite ejecutar el archivo directamente (`python proyecto/main.py`) y también
# como módulo (`python -m proyecto.main`) sin depender del directorio actual.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proyecto.orchestration.orquestador import listar_agentes, obtener_agente


def main() -> None:
    parser = argparse.ArgumentParser(description="Sistema Multi-Agente Bancario")
    parser.add_argument(
        "--consulta",
        help="Envía una consulta al Data Steward sobre la base sintética.",
    )
    args = parser.parse_args()

    if args.consulta:
        agente = obtener_agente("data_steward")
        print(agente.procesar(args.consulta))
        return

    print("=== Sistema Multi-Agente Bancario ===\n")
    print("Agentes disponibles:\n")

    for nombre, info in listar_agentes().items():
        print(f"  - {nombre}")
        print(f"      Descripción: {info['descripcion']}")
        print(f"      Modelo: {info['modelo']}")
        print(f"      Herramientas: {info['herramientas']}")
        print()


if __name__ == "__main__":
    main()
