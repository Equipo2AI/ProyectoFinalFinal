"""Comprueba Ollama; --instalar descarga solo modelos faltantes del YAML."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from proyecto.config.models import RUTA_MODELOS


def modelos_requeridos() -> list[str]:
    datos = yaml.safe_load(RUTA_MODELOS.read_text(encoding="utf-8"))
    if not isinstance(datos, dict) or not isinstance(datos.get("agents"), dict):
        raise ValueError("models.yaml debe contener un mapa 'agents'.")
    modelos = set()
    for agente, entrada in datos["agents"].items():
        if not isinstance(entrada, dict):
            raise ValueError(f"Configuracion invalida para {agente}.")
        if entrada.get("provider") != "ollama":
            continue
        modelo = entrada.get("model")
        if not isinstance(modelo, str) or not modelo.strip():
            raise ValueError(f"Falta el modelo de {agente} en models.yaml.")
        modelos.add(modelo.strip())
    if not modelos:
        raise ValueError("No hay modelos Ollama configurados en models.yaml.")
    return sorted(modelos)


def _endpoint() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return host if "://" in host else "http://" + host


def comprobar(instalar: bool = False) -> list[str]:
    requeridos = modelos_requeridos()
    with urlopen(_endpoint() + "/api/tags", timeout=10) as respuesta:
        instalados = {m["name"] for m in json.load(respuesta)["models"]}
    faltantes = [m for m in requeridos if (m if ":" in m else m + ":latest") not in instalados]
    for modelo in requeridos:
        print(f"{'FALTA' if modelo in faltantes else 'OK'}: {modelo}", flush=True)
    if instalar:
        for modelo in faltantes:
            print(f"Descargando {modelo} (puede ocupar varios GB)...", flush=True)
            solicitud = Request(
                _endpoint() + "/api/pull",
                data=json.dumps({"model": modelo, "stream": True}).encode(),
                headers={"Content-Type": "application/json"},
            )
            terminado = False
            with urlopen(solicitud, timeout=120) as respuesta:
                for linea in respuesta:
                    evento = json.loads(linea)
                    if evento.get("error"):
                        raise ValueError(f"No se pudo instalar {modelo}: {evento['error']}")
                    if evento.get("status") == "success":
                        terminado = True
            if not terminado:
                raise ValueError(f"Descarga incompleta de {modelo}; vuelve a ejecutar --instalar.")
            print(f"Instalado: {modelo}", flush=True)
        if faltantes:
            return comprobar()
    return faltantes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instalar", action="store_true", help="Descargar modelos faltantes")
    args = parser.parse_args()
    try:
        faltantes = comprobar(args.instalar)
    except HTTPError as error:
        print(f"Error HTTP {error.code} de Ollama; verifica el servicio y el nombre del modelo.", file=sys.stderr)
        return 2
    except (URLError, TimeoutError, ConnectionError):
        print("Ollama no esta disponible. Abre Ollama o ejecuta 'ollama serve' y revisa OLLAMA_HOST.", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"No se pudieron preparar los modelos: {error}", file=sys.stderr)
        return 2
    if faltantes:
        print("Faltan modelos. Ejecuta: python -m proyecto.config.preparar_modelos --instalar", file=sys.stderr)
        return 1
    print("Todos los modelos configurados estan disponibles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
