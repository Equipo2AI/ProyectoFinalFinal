"""Data Steward for the project's synthetic customer CSV."""

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "clientes.csv"


def _normalizar_consulta(texto: str) -> str:
    """Normalize text so the privacy guard handles accents consistently."""
    normalizado = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(
        caracter for caracter in normalizado if not unicodedata.combining(caracter)
    )


def _solicita_registros_personales(consulta: str) -> bool:
    """Detect requests to reveal personal fields or individual customer records."""
    texto = _normalizar_consulta(consulta)
    campos_personales = re.search(
        r"\b(?:nombres?|correos?|emails?|telefonos?|celulares?|"
        r"direcciones?|domicilios?|saldos?|curp|rfc|datos personales|"
        r"registros? personales|informacion personal|clientes?)\b",
        texto,
    )
    accion_de_revelar = re.search(
        r"\b(?:muestr\w*|mostr\w*|dame|give|show|list\w*|enumera\w*|"
        r"proporcion\w*|revela\w*|comparte\w*|imprime\w*|exporta\w*|"
        r"descarga\w*|extrae\w*|lee|obt[e]?n\w*|recupera\w*|ver)\b",
        texto,
    )
    pregunta_por_valores = re.search(
        r"\b(?:cuales? son|que\s+\w+\s+(?:hay|tiene|aparecen)|"
        r"what\s+(?:are|is)|which\s+\w+\s+(?:are|is))\b",
        texto,
    )
    return bool(campos_personales and (accion_de_revelar or pregunta_por_valores))


def _load_dataset() -> pd.DataFrame:
    """Load the configured example dataset without exposing its records."""
    return pd.read_csv(DATA_FILE)


@tool
def inspect_customer_dataset() -> str:
    """Return row count, column names, inferred types, and missing counts."""
    try:
        df = _load_dataset()
    except FileNotFoundError:
        return json.dumps({"error": "No se encontró el CSV de clientes."}, ensure_ascii=False)
    except pd.errors.EmptyDataError:
        return json.dumps({"error": "El CSV de clientes está vacío."}, ensure_ascii=False)
    except Exception:
        return json.dumps({"error": "No se pudo leer el CSV de clientes."}, ensure_ascii=False)

    return json.dumps(
        {
            "archivo": DATA_FILE.name,
            "filas": int(len(df)),
            "columnas": [
                {
                    "nombre": str(column),
                    "tipo_detectado": str(df[column].dtype),
                    "valores_faltantes": int(df[column].isna().sum()),
                }
                for column in df.columns
            ],
        },
        ensure_ascii=False,
    )


@tool
def detect_customer_data_quality_issues() -> str:
    """Count missing values, exact duplicate rows, and repeated customer IDs."""
    try:
        df = _load_dataset()
    except FileNotFoundError:
        return json.dumps({"error": "No se encontró el CSV de clientes."}, ensure_ascii=False)
    except pd.errors.EmptyDataError:
        return json.dumps({"error": "El CSV de clientes está vacío."}, ensure_ascii=False)
    except Exception:
        return json.dumps({"error": "No se pudo leer el CSV de clientes."}, ensure_ascii=False)

    missing = {
        str(column): int(count)
        for column, count in df.isna().sum().items()
        if count > 0
    }
    duplicate_rows = int(df.duplicated().sum())

    repeated_customer_ids = None
    if "id_cliente" in df.columns:
        customer_ids = df["id_cliente"].dropna()
        repeated_customer_ids = int(
            customer_ids.duplicated(keep=False).sum()
        )

    return json.dumps(
        {
            "archivo": DATA_FILE.name,
            "filas_con_algun_valor_faltante": int(df.isna().any(axis=1).sum()),
            "valores_faltantes_por_columna": missing,
            "filas_duplicadas_exactas": duplicate_rows,
            "filas_con_id_cliente_repetido": repeated_customer_ids,
            "nota": (
                "Un identificador repetido es un hallazgo para revisar; "
                "no confirma por sí solo que los registros deban eliminarse."
            ),
        },
        ensure_ascii=False,
    )


class DataStewardAgent:
    def __init__(self):
        self.nombre = "data_steward"
        self.descripcion = "Revisa la estructura y calidad del CSV de clientes con resultados agregados."
        self.modelo = "llama3.1:latest"
        self.herramientas = [
            inspect_customer_dataset.name,
            detect_customer_data_quality_issues.name,
        ]
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            llm = ChatOllama(model=self.modelo, temperature=0)
            self._agent = create_agent(
                model=llm,
                tools=[
                    inspect_customer_dataset,
                    detect_customer_data_quality_issues,
                ],
                system_prompt=(
                    "Eres un Data Steward que ayuda a entender y revisar la calidad "
                    "del CSV sintético proyecto/data/clientes.csv. Responde en español "
                    "claro y profesional. Para preguntas sobre estructura o calidad, "
                    "consulta las herramientas y basa tus conclusiones en sus resultados. "
                    "Las herramientas solo devuelven conteos y nombres de columnas; no "
                    "solicites ni reveles registros personales. No inventes reglas de "
                    "negocio, definiciones, permisos ni causas. Distingue los hechos "
                    "observados de las hipótesis. Un faltante no significa cero; un ID "
                    "repetido requiere revisión y no demuestra por sí mismo que haya que "
                    "borrar un registro. No modifiques datos ni afirmes haberlo hecho. "
                    "El catálogo de metadatos actual no describe las columnas de este CSV, "
                    "así que no lo uses para atribuirles significados."
                ),
            )
        return self._agent

    def procesar(self, consulta: str) -> str:
        if _solicita_registros_personales(consulta):
            return (
                "No puedo mostrar ni inferir datos personales de clientes "
                "ni valores de registros individuales. Sí puedo ayudar con "
                "resultados agregados, como conteos de faltantes, duplicados "
                "y tipos de datos."
            )

        result = self._get_agent().invoke(
            {"messages": [{"role": "user", "content": consulta}]}
        )
        return result["messages"][-1].content

    def __repr__(self) -> str:
        return f"<Agente {self.nombre} | modelo: {self.modelo}>"


data_steward_agent = DataStewardAgent()
