# Simple entry point for the multi-agent banking system.

from orchestration.orquestador import listar_agentes, obtener_agente


def main() -> None:
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
