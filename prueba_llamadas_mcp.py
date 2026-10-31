from proyecto.agents.data_steward_agent import data_steward_agent

consulta = (
    "Inspecciona la estructura del dataset y revisa su calidad. "
    "Utiliza las dos herramientas disponibles."
)

agente = data_steward_agent._get_agent()

resultado = agente.invoke({
    "messages": [{"role": "user", "content": consulta}]
})

print("\n=== HERRAMIENTAS UTILIZADAS ===")

for mensaje in resultado["messages"]:
    for llamada in getattr(mensaje, "tool_calls", []):
        print("Herramienta solicitada:", llamada["name"])

    if mensaje.type == "tool":
        print("Herramienta ejecutada:", mensaje.name)

print("\n=== RESPUESTA FINAL ===")
print(resultado["messages"][-1].content)
