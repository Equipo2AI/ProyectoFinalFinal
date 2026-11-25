"""Filtro local de tipos documentales; independiente del corpus normativo.

Solo persiste referencias y calibracion. Nunca guarda texto/vectores de uploads.
Los documentos son datos de comparacion, no instrucciones ni normativa oficial.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

MODEL = "bge-large:latest"
VERSION = "cosine-mean-nearest-600chars-overlap100-24-v1"


class SimilarityError(RuntimeError):
    """Fallo tecnico; no equivale a irrelevancia."""


def storage() -> Path:
    override = os.getenv("PROYECTO_DOCUMENT_REFERENCES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/share")))
    project = hashlib.sha256(str(Path(__file__).resolve().parents[2]).casefold().encode()).hexdigest()[:12]
    return base / "ProyectoFinalFinal" / "document_similarity" / project


def _api(endpoint: str, payload=None):
    from urllib.parse import urlparse
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if "://" not in host:
        host = "http://" + host
    parsed = urlparse(host)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SimilarityError("La validacion privada requiere Ollama local.")
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(host + endpoint, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        # No incluir cuerpo HTTP, texto del archivo ni rutas privadas en errores.
        raise SimilarityError(f"Ollama no pudo completar embeddings ({type(exc).__name__}). Comprueba el servicio y {MODEL}.") from exc


def model_digest() -> str:
    models = _api("/api/tags").get("models", [])
    match = next((m for m in models if m.get("name") == MODEL), None)
    if not match or not match.get("digest"):
        raise SimilarityError(f"Falta {MODEL}. Instalalo con: ollama pull {MODEL}")
    return match["digest"]


def chunks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(re.findall(r"[^\W\d_]", text, re.UNICODE)) < 40:
        return []
    parts = [text[i:i + 600] for i in range(0, len(text), 500)]
    parts = [p for p in parts if len(p) >= 40]
    if len(parts) > 24:
        parts = [parts[round(i * (len(parts) - 1) / 23)] for i in range(24)]
    return parts


def embed(parts: list[str]) -> list[list[float]]:
    vectors = []
    for start in range(0, len(parts), 8):
        batch = parts[start:start + 8]
        result = _api("/api/embed", {"model": MODEL, "input": batch, "truncate": False})
        values = result.get("embeddings", [])
        if len(values) != len(batch):
            raise SimilarityError("Ollama devolvio embeddings incompletos.")
        for vector in values:
            if not vector or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
                raise SimilarityError("Ollama devolvio un vector invalido.")
            norm = math.sqrt(sum(x*x for x in vector))
            if not norm:
                raise SimilarityError("Ollama devolvio un vector nulo.")
            vectors.append([x / norm for x in vector])
    if not vectors or len({len(v) for v in vectors}) != 1:
        raise SimilarityError("No hay embeddings consistentes.")
    return vectors


def scores(vectors, references):
    result = {}
    for ref in references:
        if not ref["vectors"] or any(len(v) != len(vectors[0]) for v in ref["vectors"]):
            raise SimilarityError("Dimensiones incompatibles; prepara de nuevo las referencias.")
        # Cada fragmento nuevo aporta su mejor coincidencia, sin que un unico
        # parrafo bancario permita aceptar un archivo mayoritariamente ajeno.
        result[ref["name"]] = sum(
            max(sum(a*b for a, b in zip(v, r)) for r in ref["vectors"])
            for v in vectors
        ) / len(vectors)
    return result


def _save(data):
    root = storage()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
        json.dump(data, stream, ensure_ascii=False)
        temporary = Path(stream.name)
    temporary.replace(root / "references.json")


def prepare(paths: list[str]):
    """Registro explicito local. No copia PDFs al proyecto ni al indice RAG."""
    from proyecto.agents.document_intelligence_agent import _convertir_documento, _resolver_documento
    if len(paths) < 2:
        raise SimilarityError("Se requieren al menos dos referencias.")
    digest = model_digest()
    refs = []
    for path in paths:
        file = _resolver_documento(path)
        if file.suffix.lower() != ".pdf":
            raise SimilarityError("Las referencias deben ser PDF.")
        text = _convertir_documento(file)
        if text.startswith("MarkItDown no extrajo") or "[Contenido truncado:" in text:
            raise SimilarityError("Extraccion de referencia vacia o truncada.")
        parts = chunks(text)
        if not parts:
            raise SimilarityError("Referencia sin texto suficiente.")
        refs.append({"name": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "vectors": embed(parts)})
    if len({r["sha256"] for r in refs}) != len(refs) or len({r["name"] for r in refs}) != len(refs):
        raise SimilarityError("Las referencias deben ser distintas.")
    _save({"model": MODEL, "digest": digest, "version": VERSION, "references": refs})
    return {"model": MODEL, "references": [{k:v for k,v in r.items() if k != "vectors"} for r in refs]}


def _load():
    try:
        data = json.loads((storage() / "references.json").read_text(encoding="utf-8"))
        if data["model"] != MODEL or data["version"] != VERSION or data["digest"] != model_digest():
            raise SimilarityError("Cambio el modelo o algoritmo; prepara y calibra de nuevo las referencias.")
        if len(data["references"]) < 2:
            raise ValueError()
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SimilarityError("Referencias locales ausentes o invalidas. Ejecuta la preparacion y calibracion documental.") from exc


def calibrate(examples: list[dict], *, allow_overlap: bool = False):
    """Ejemplos independientes del lote final; modo estricto por defecto.

    Opt-in administrativo: admite límites indeterminados y contiene todo el
    solapamiento en la banda sin decisión automática. No certifica generalización.
    """
    data = _load()
    results = []
    for example in examples:
        labels = {"aceptado", "rechazado", "indeterminado"} if allow_overlap else {"aceptado", "rechazado"}
        if example["expected"] not in labels:
            raise SimilarityError("Etiqueta de calibracion invalida.")
        parts = chunks(example["text"])
        if not parts:
            raise SimilarityError("Ejemplo de calibracion sin texto suficiente.")
        similarities = scores(embed(parts), data["references"])
        results.append({"id": example["id"], "expected": example["expected"], "similarities": similarities,
                        "sha256": hashlib.sha256(example["text"].encode()).hexdigest()})
    positives = [max(r["similarities"].values()) for r in results if r["expected"] == "aceptado"]
    negatives = [max(r["similarities"].values()) for r in results if r["expected"] == "rechazado"]
    if len(positives) < 2 or len(negatives) < 2:
        raise SimilarityError("La calibracion no separa los tipos admitidos de los ajenos. No se habilita el filtro.")
    low, high = min(positives), max(negatives)
    overlap = low <= high
    if overlap and not allow_overlap:
        raise SimilarityError("La calibracion no separa los tipos admitidos de los ajenos. No se habilita el filtro.")
    # Opt-in administrativo: el solapamiento no fuerza ninguna etiqueta.
    # nextafter evita aceptar un negativo o rechazar un positivo en el borde.
    accept = math.nextafter(high, math.inf) if overlap else low
    reject = math.nextafter(low, -math.inf) if overlap else high
    boundaries = [max(r['similarities'].values()) for r in results if r['expected'] == 'indeterminado']
    if boundaries:
        accept = max(accept, math.nextafter(max(boundaries), math.inf))
        reject = min(reject, math.nextafter(min(boundaries), -math.inf))
    if not -1 <= reject < accept <= 1:
        raise SimilarityError('La calibracion no permite una banda segura.')
    data["calibration"] = {"accept": accept, "reject": reject, "examples": results,
                           "method": "conservative-boundaries" if boundaries else "conservative-overlap" if overlap else "separated-envelope",
                           "overlap": overlap}
    _save(data)
    return data["calibration"]


def evaluate(text: str) -> dict:
    start = time.perf_counter()
    try:
        if text.startswith(("MarkItDown no extrajo", "No se pudo convertir el documento")) or "[Contenido truncado:" in text:
            raise SimilarityError("Extraccion vacia, fallida o truncada; no se puede clasificar el documento.")
        data = _load()
        calibration = data.get("calibration")
        if not calibration:
            raise SimilarityError("Falta calibrar el filtro documental con ejemplos contrastados.")
        parts = chunks(text)
        if not parts:
            return {"decision": "indeterminado", "model": MODEL, "similarities": {},
                    "message": "No hay texto suficiente para determinar el tipo documental; aporta un documento legible mas completo.",
                    "seconds": round(time.perf_counter() - start, 3)}
        similarities = scores(embed(parts), data["references"])
        best = max(similarities.values())
        decision = "aceptado" if best >= calibration["accept"] else "rechazado" if best <= calibration["reject"] else "indeterminado"
        messages = {
            "aceptado": "Documento compatible con las referencias internas de tipos admitidos.",
            "rechazado": "El documento no alcanza la similitud necesaria con las referencias internas de tipos admitidos.",
            "indeterminado": "La similitud no permite confirmar el tipo documental. No se ha rechazado por irrelevancia; aporta mas evidencia o solicita revision.",
        }
        return {"decision": decision, "model": MODEL, "similarities": similarities,
                "thresholds": {"accept": calibration["accept"], "reject": calibration["reject"]},
                "chunks": len(parts), "message": messages[decision], "seconds": round(time.perf_counter() - start, 3)}
    except (SimilarityError, KeyError, TypeError, ValueError) as exc:
        return {"decision": "error_tecnico", "model": MODEL, "similarities": {},
                "message": str(exc) if isinstance(exc, SimilarityError) else "Almacen documental invalido; prepara de nuevo las referencias.",
                "seconds": round(time.perf_counter() - start, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--referencias", nargs="+")
    parser.add_argument("--calibracion", type=Path, help="JSON con id, text y expected (solo ejemplos sinteticos)")
    args = parser.parse_args()
    try:
        if args.referencias:
            print(json.dumps(prepare(args.referencias), ensure_ascii=False))
        if args.calibracion:
            print(json.dumps(calibrate(json.loads(args.calibracion.read_text(encoding="utf-8"))), ensure_ascii=False))
        if not args.referencias and not args.calibracion:
            parser.error("Indica --referencias o --calibracion")
    except (SimilarityError, OSError, ValueError) as exc:
        parser.exit(1, f"No se pudo preparar el filtro: {exc}\n")


if __name__ == "__main__":
    main()
