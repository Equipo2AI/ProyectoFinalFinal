# Simple entry point for the multi-agent banking system.

from orchestration.orquestador import listar_agentes, obtener_agente

AGENTE_DE_PRUEBA = "normatividad_rag"


def main() -> None:
    print("=== Sistema Multi-Agente Bancario ===\n")
    print("Agentes disponibles:\n")

    for nombre, info in listar_agentes().items():
        print(f"  - {nombre}")
        print(f"      Descripción: {info['descripcion']}")
        print(f"      Modelo: {info['modelo']}")
        print(f"      Herramientas: {info['herramientas']}")
        print()

    agente = obtener_agente(AGENTE_DE_PRUEBA)
    if agente is None:
        print(f"No existe el agente '{AGENTE_DE_PRUEBA}'.")
        return

    if not hasattr(agente, "procesar"):
        print(f"El agente '{AGENTE_DE_PRUEBA}' no tiene método procesar().")
        return

    print(f"Probando con: {AGENTE_DE_PRUEBA} (escribe 'salir' para terminar)\n")

    while True:
        consulta = input("Consulta> ").strip()

        if not consulta:
            continue
        if consulta.lower() in {"salir", "exit", "quit"}:
            break

        try:
            respuesta = agente.procesar(consulta)
        except Exception as e:
            print(f"\n[Error] {type(e).__name__}: {e}\n")
            continue

        print(f"\n{respuesta}\n")


if __name__ == "__main__":
    main()