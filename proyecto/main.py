"""Entry point for listing agents or asking the Data Steward a question."""

import argparse

from orchestration.orquestador import listar_agentes, obtener_agente


def main() -> None:
    parser = argparse.ArgumentParser(description="Sistema Multi-Agente Bancario")
    parser.add_argument(
        "--consulta",
        help="Envía una consulta al Data Steward usando clientes.csv.",
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
