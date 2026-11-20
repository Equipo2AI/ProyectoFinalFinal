"""Comando de validación del catálogo semántico.

Uso:
    python -m proyecto.semantic.validar
    python -m proyecto.semantic.validar --json

Sale con código 1 si el catálogo no coincide con la base o si una relación
declarada tiene huérfanos.
"""

from __future__ import annotations

import argparse
import json
import sys

from proyecto.semantic.catalogo import construir_catalogo, validar_catalogo


def _imprimir_humano(resultado: dict) -> None:
    resumen = resultado.get("resumen", {})
    print("Catálogo semántico")
    print(f"  Tablas: {resumen.get('tablas', 0)}")
    print(f"  Vistas: {resumen.get('vistas', 0)}")
    print(f"  Columnas: {resumen.get('columnas', 0)}")
    print(f"  Métricas: {resumen.get('metricas', 0)}")
    print(f"  Métricas runtime: {resumen.get('metricas_runtime', 0)}")
    print(f"  Reglas: {resumen.get('reglas', 0)}")
    print(f"  Relaciones: {resumen.get('relaciones', 0)}")
    print(f"  Relaciones validadas: {resumen.get('relaciones_validadas', 0)}")
    print(f"  Columnas con metadatos genéricos: {resumen.get('columnas_sin_curar', 0)}")
    print()

    errores = resultado.get("errores", [])
    advertencias = resultado.get("advertencias", [])
    if errores:
        print(f"Errores ({len(errores)}):")
        for error in errores:
            print(f"  - {error}")
    else:
        print("Errores: 0")

    if advertencias:
        print(f"Advertencias ({len(advertencias)}):")
        for advertencia in advertencias:
            print(f"  - {advertencia}")

    print()
    print("Resultado:", "OK" if resultado.get("ok") else "FALLÓ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida el catálogo semántico contra la base sintética."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime el resultado completo en JSON.",
    )
    parser.add_argument(
        "--catalogo",
        action="store_true",
        help="Imprime el catálogo construido en JSON (no valida).",
    )
    args = parser.parse_args(argv)

    if args.catalogo:
        print(json.dumps(construir_catalogo(), ensure_ascii=False, indent=2))
        return 0

    resultado = validar_catalogo()
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        _imprimir_humano(resultado)
    return 0 if resultado.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
