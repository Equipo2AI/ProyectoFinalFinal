# Simple entry point for the multi-agent banking system.

from orchestration.orquestador import listar_agentes, orquestador


def main() -> None:
    print("=== Sistema Multi-Agente Bancario ===\n")
    print("Agentes disponibles:\n")

    for nombre, info in listar_agentes().items():
        print(f"  - {nombre}")
        print(f"      Descripción: {info['descripcion']}")
        print(f"      Modelo: {info['modelo']}")
        print(f"      Herramientas: {info['herramientas']}")
        print()

    print("Escribe tu consulta ('reset' reinicia la conversación, 'salir' termina)\n")

    while True:
        consulta = input("Consulta> ").strip()

        if not consulta:
            continue
        if consulta.lower() in {"salir", "exit", "quit"}:
            break
        if consulta.lower() == "reset":
            orquestador.reiniciar()
            print("Conversación reiniciada.\n")
            continue

        try:
            respuesta = orquestador.procesar(consulta)
        except Exception as e:
            print(f"\n[Error] {type(e).__name__}: {e}\n")
            continue

        agentes = ", ".join(orquestador.agentes_invocados) or "ninguno"
        print(f"\n[Agentes invocados: {agentes}]")
        print(f"\n{respuesta}\n")


if __name__ == "__main__":
    main()