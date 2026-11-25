"""Regresiones focalizadas y un lote MCP real optativo; sin modelo generativo."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from proyecto.agents import document_similarity as ds


def test_coseno_y_fragmentos_representativos():
    assert ds.scores([[1., 0.], [0., 1.]], [{"name": "r", "vectors": [[1., 0.]]}]) == {"r": .5}
    parts = ds.chunks("inicio " + "datos bancarios " * 3000 + " final")
    assert len(parts) == 24 and parts[0].startswith("inicio") and parts[-1].endswith("final")


def test_indeterminado_y_fallo_tecnico_no_son_rechazo(monkeypatch):
    monkeypatch.setattr(ds, "_load", lambda: {"calibration": {"accept": .8, "reject": .4}, "references": []})
    monkeypatch.setattr(ds, "embed", Mock(side_effect=AssertionError("No debe inferir")))
    assert ds.evaluate("poco texto")["decision"] == "indeterminado"
    assert ds.evaluate("MarkItDown no extrajo texto")["decision"] == "error_tecnico"
    monkeypatch.setattr(ds, "_load", Mock(side_effect=ds.SimilarityError("Ollama no disponible")))
    assert ds.evaluate("datos " * 30)["decision"] == "error_tecnico"


def test_banda_intermedia_y_persistencia_privada(monkeypatch, tmp_path):
    monkeypatch.setenv("PROYECTO_DOCUMENT_REFERENCES_DIR", str(tmp_path / "privado"))
    ds._save({"references": [{"name": "solo referencia"}]})
    before = (ds.storage() / "references.json").read_bytes()
    monkeypatch.setattr(ds, "_load", lambda: {"calibration": {"accept": .8, "reject": .4}, "references": []})
    monkeypatch.setattr(ds, "embed", lambda parts: [[1., 0.]])
    monkeypatch.setattr(ds, "scores", lambda vectors, refs: {"ref": .7})
    assert ds.evaluate("texto sintetico " * 20)["decision"] == "indeterminado"
    assert (ds.storage() / "references.json").read_bytes() == before


def test_panel_streamlit_muestra_validacion_sin_texto_privado():
    import ast
    from streamlit.testing.v1 import AppTest
    path = str(Path("proyecto/ui/app.py").resolve())
    source = f'''
import ast
from pathlib import Path
from types import SimpleNamespace
import streamlit as st
tree = ast.parse(Path({path!r}).read_text(encoding="utf-8"))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "panel_consola")
namespace = {{"st": st, "componentes": SimpleNamespace(traza=lambda pasos: None), "_pasos": lambda artefacto: []}}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "<panel>", "exec"), namespace)
namespace["panel_consola"]({{"validacion_documental": {{"decision": "indeterminado", "model": "bge-large:latest", "similarities": {{"referencia": 0.7}}}}}}, False)
'''
    app = AppTest.from_string(source).run()
    assert not app.exception and len(app.json) == 1


@pytest.mark.parametrize("decision", ["rechazado", "indeterminado", "error_tecnico", "aceptado"])
def test_puerta_antes_del_orquestador(tmp_path, monkeypatch, decision):
    """Dobles de frontera: solo el caso aceptado alcanza el generador."""
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    from proyecto.security import local_auth as auth, document_access as access
    from proyecto.agents import document_questions as dq
    from contextlib import nullcontext
    import streamlit as st
    class State(dict):
        __getattr__ = dict.get
        __setattr__ = dict.__setitem__
    monkeypatch.setattr(st, 'session_state', State())
    file = tmp_path / "sintetico.txt"
    text = ('Estado de cuenta de débito sintético\nSaldo inicial: 100.00\n'
            'Total de abonos: 20.00\nTotal de cargos: 10.00\nSaldo final: 110.00')
    file.write_text(text, encoding="utf-8")
    pages = [{'source': 'Sección 1', 'text': text}]
    context = {'document_id': 'doc-simulado', 'owner': 'owner-controlado',
               'admission': {'decision': 'aceptado'}, 'pages': pages,
               'integrity': dq.validate_integrity(pages), 'metadata': dq.document_metadata(pages)}
    assert context['integrity']['state'] == 'VALIDO'
    monkeypatch.setattr(auth, 'authenticated', lambda token: nullcontext())
    monkeypatch.setattr(access, 'authorize_file', lambda ref: ('doc-simulado', {'path': str(file)}))
    def authorize_context(value):
        assert value == context and value['owner'] == 'owner-controlado'
    context_guard = Mock(side_effect=authorize_context)
    monkeypatch.setattr(access, 'authorize_context', context_guard)
    monkeypatch.setattr(servicio, 'authorize_file_access', lambda *args, **kwargs: file)
    monkeypatch.setattr(servicio, "_identidad_documental", lambda *a: ("owner-controlado", "token-de-prueba"))
    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", lambda *a, **kw: text)
    def call(module, name, args):
        assert module == 'proyecto.mcp_servers.document_intelligence_server'
        if name == 'validar_tipo_documental':
            assert args == {'documento_id': 'doc-simulado'}
            return json.dumps({'decision': decision, 'message': decision})
        assert decision == 'aceptado' and name == 'preparar_documento_consultable'
        assert args == {'file_path': 'doc-simulado', 'texto': ''}
        return json.dumps(context)
    monkeypatch.setattr(mcp_client, "llamar_herramienta", call)
    generator = Mock(return_value={"respuesta": "frontera aceptada"})
    monkeypatch.setattr(servicio, "_responder_con_archivo", generator)
    result = servicio.procesar_documento(str(file))
    assert result["validacion_documental"]["decision"] == decision
    assert generator.call_count == (1 if decision == "aceptado" else 0)
    assert context_guard.call_count == (1 if decision == 'aceptado' else 0)
    if decision == 'aceptado':
        assert result['integridad_documental']['state'] == 'VALIDO'


def _pdf(path, text):
    """PDF selectable sintetico, sin dependencia adicional ni datos personales."""
    import textwrap
    lines = [line for p in text.splitlines() for line in (textwrap.wrap(p, 85) or [""])]
    escape = lambda x: x.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = ("BT /F1 10 Tf 35 800 Td 13 TL " + " ".join(f"({escape(line)}) Tj T*" for line in lines) + " ET").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    output = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets)
    output += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(output)


@pytest.mark.skipif(os.getenv("VALIDAR_EMBEDDINGS_REAL") != "1", reason="Requiere referencias privadas preparadas y Ollama")
def test_lote_mcp_real(tmp_path, monkeypatch, identidad_prueba):
    from fastmcp import Client
    from proyecto.mcp_servers.mcp_client import crear_transporte
    from proyecto import servicio
    from proyecto.mcp_servers import mcp_client
    from proyecto.security.document_access import store_upload
    from proyecto.security.local_auth import principal
    examples = json.loads((Path(__file__).parent / "fixtures/document_similarity_validation.json").read_text(encoding="utf-8"))
    cache = ds.storage() / "references.json"
    before = cache.read_bytes()
    cache_mtime = cache.stat().st_mtime_ns
    db = Path(principal()['database'])
    db_before = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    results = []

    async def run():
        async with Client(crear_transporte("proyecto.mcp_servers.document_intelligence_server"), timeout=180) as client:
            for example in examples:
                file = tmp_path / (example["id"] + ".pdf")
                customer = principal()['customers'][0]
                text_fixture = example['text'].replace('Cuenta DEMO-Z9', f'Cliente: {customer}').replace('cuenta bancaria ficticia DEMO-N', f'cuenta bancaria del cliente {customer}')
                _pdf(file, text_fixture)
                reference = store_upload(file.read_bytes(), '.pdf', file.name)
                extracted = await client.call_tool("convertir_documento_markdown", {"file_path": reference})
                assert not extracted.is_error
                text = "\n".join(b.text for b in extracted.content if b.type == "text")
                assert not text.startswith(("No se pudo", "MarkItDown no extrajo"))
                validated = await client.call_tool("validar_tipo_documental", {"documento_id": reference})
                assert not validated.is_error
                result = json.loads("\n".join(b.text for b in validated.content if b.type == "text"))
                results.append({"id": example["id"], **result})
                if result["decision"] == "rechazado":
                    # Reutiliza el resultado MCP real; espia solo en la frontera
                    # generativa. No se presenta como una inferencia MiMo.
                    monkeypatch.setattr(servicio, "_llamar_mcp_document_intelligence", lambda *a, **kw: text)
                    monkeypatch.setattr(mcp_client, "llamar_herramienta", lambda *a, **kw: json.dumps(result))
                    generator = Mock(side_effect=AssertionError("El rechazo no puede llamar al generador"))
                    monkeypatch.setattr(servicio, "_responder_con_archivo", generator)
                    assert servicio.procesar_documento(reference)["validacion_documental"]["decision"] == "rechazado"
                    generator.assert_not_called()
    asyncio.run(run())
    print("RESULTADOS_EMBEDDINGS=" + json.dumps(results, ensure_ascii=False))
    assert cache.read_bytes() == before and cache.stat().st_mtime_ns == cache_mtime
    if db_before:
        assert hashlib.sha256(db.read_bytes()).hexdigest() == db_before
    for example, result in zip(examples, results):
        # La carta narrativa no tiene por que superar un umbral calibrado con
        # estados: se exige no confundir falta de evidencia con irrelevancia.
        expected = example["expected"]
        assert result["decision"] in (expected if isinstance(expected, list) else [expected]), result
