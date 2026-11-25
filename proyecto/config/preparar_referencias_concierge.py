"""Preparación administrativa explícita de referencias sintéticas; nunca uploads.

Usa el caché y calibrador existentes. El caché activo solo cambia con --aplicar
si la calibración separa los ejemplos y los controles independientes son seguros.
No importa agentes ni herramientas de extracción de documentos privados.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import time

from dotenv import load_dotenv
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests' / 'fixtures'
DATASET = FIXTURES / 'document_similarity_concierge.json'
REFERENCES = FIXTURES / 'document_similarity_concierge_references'
REPORT = FIXTURES / 'document_similarity_concierge_results.json'
STRUCTURAL_DATASET = FIXTURES / 'document_similarity_concierge_structural_validation.json'
STRUCTURAL_REPORT = FIXTURES / 'document_similarity_concierge_structural_results.json'


def structural_validation_fingerprint():
    """Invalida la aprobación si cambia el perfil, el candidato o el lote fijo."""
    files = [Path(__file__), ROOT / 'proyecto/agents/document_requirements.py', STRUCTURAL_DATASET]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def validated_structural_types():
    """Aprobación local administrativa; nunca procede del documento o del cliente."""
    try:
        report = json.loads(STRUCTURAL_REPORT.read_text(encoding='utf-8'))
        if (report.get('passed') is True and report.get('fingerprint') == structural_validation_fingerprint()
                and report.get('false_positives') == 0 and report.get('false_negatives') == 0
                and report.get('unexpected_indeterminate') == 0):
            return frozenset(report['validated_types']) & {'ine', 'curp', 'domicilio'}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return frozenset()


def contrast_candidate(semantic, pages, references, *, incomplete=False, validated_types=frozenset()):
    """Contrasta una decisión candidata con metadatos de extracción confiable.

    Solo preparación administrativa: no cambia evaluate ni el caché activo.
    Los indicadores reconocen estructura, no autenticidad o vigencia.
    No pasar aquí metadatos o páginas aportados como acreditación por un cliente.
    """
    from proyecto.agents.document_requirements import requirement_metadata
    result = copy.deepcopy(semantic)
    result['semantic_decision'] = semantic.get('decision')
    if result.get('decision') == 'error_tecnico':
        return result
    meta = requirement_metadata(pages, incomplete)
    aliases = {'identificacion': 'ine', 'curp': 'curp', 'domicilio': 'domicilio'}
    similarities = result.get('similarities', {})
    best = max(similarities.values(), default=None)
    winners = {aliases.get(ref.get('document_type')) for ref in references
               if best is not None and similarities.get(ref['name']) == best}
    recognition = meta['recognition']
    kind = recognition['type']
    individual_record = (recognition['individual_document'] and recognition['single_record_block']
                         and recognition['full_heading_matches'])
    coherent = meta['readable'] and winners == {kind} and recognition['sufficient'] and individual_record
    result['structure'] = {'type': kind, 'readable': meta['readable'],
                           'reference_type_matches': kind is not None and winners == {kind},
                           'field_evidence_sufficient': recognition['sufficient'],
                           'recognition': recognition,
                           'authenticity_verified': False, 'validity_evaluated': False}
    if recognition['multiple_types']:
        result.update(decision='indeterminado', admission_basis='tipos_contradictorios',
                      message='Se detectaron varios tipos documentales sin poder atribuir los campos a un único documento. Requiere revisión.')
        return result
    # Solo el preparador administrativo puede habilitar perfiles evaluados.
    # La semántica queda en la traza; no sustituye la evidencia del documento.
    if (meta['readable'] and recognition['sufficient'] and individual_record and kind in validated_types
            and similarities):
        result.update(decision='aceptado', admission_basis='estructura_validada',
                      message='Tipo documental reconocido por estructura validada; no certifica autenticidad, titularidad, vigencia ni cumplimiento.')
        return result
    if ((result.get('decision') == 'aceptado' and not coherent)
            or (result.get('decision') == 'rechazado' and kind is not None)):
        result['decision'] = 'indeterminado'
        result['message'] = ('La evidencia estructural o semántica no permite una aceptación verificada. '
                             'El umbral global no basta para rechazar un tipo documental reconocido. '
                             'Se requiere revisión; no se determina integridad, vigencia ni autenticidad.')
    return result


def evaluate_candidate(text, pages, references, *, incomplete=False):
    """Entrada solo candidata; documentos breves conservan su texto sin relleno."""
    from proyecto.agents import document_similarity as ds
    from proyecto.agents.document_requirements import requirement_metadata
    semantic = ds.evaluate(text)
    meta = requirement_metadata(pages, incomplete)
    if (semantic.get('decision') == 'indeterminado' and not semantic.get('similarities')
            and meta['recognition']['sufficient']):
        try:
            # Excepción local: no altera chunks() para las cuatro referencias.
            similarities = ds.scores(ds.embed([text]), references)
            semantic = {**semantic, 'similarities': similarities,
                        'message': 'Documento breve: evidencia complementaria sin calibración específica; requiere revisión.'}
        except ds.SimilarityError:
            return {'decision': 'error_tecnico', 'message': 'No se pudo evaluar el documento breve.'}
    return contrast_candidate(semantic, pages, references, incomplete=incomplete,
                              validated_types=validated_structural_types())


def generate_references(dataset):
    # Reutiliza solo el escritor PDF sintético existente, sin importar los tests.
    tree = ast.parse((ROOT / 'tests' / 'test_document_similarity.py').read_text(encoding='utf-8'))
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_pdf')
    namespace = {}
    exec(compile(ast.Module(body=[writer], type_ignores=[]), '<synthetic-pdf-writer>', 'exec'), namespace)
    REFERENCES.mkdir(exist_ok=True)
    result = []
    texts = set()
    for item in dataset['references']:
        name = item['name']
        if Path(name).name != name or not name.endswith('_simulada.pdf'):
            raise ValueError('Nombre de referencia sintética no permitido.')
        text = dataset['notice'] + '\n' + item['text']
        if text in texts:
            raise ValueError('Referencias repetidas.')
        texts.add(text)
        path = REFERENCES / name
        namespace['_pdf'](path, text)
        extracted = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
        if 'SIMULACION' not in extracted:
            raise ValueError('Falta marca visible de simulación.')
        result.append((item, path, extracted))
    return result


def prepare(apply=False):
    load_dotenv(ROOT / '.env')
    from proyecto.agents import document_similarity as ds
    dataset = json.loads(DATASET.read_text(encoding='utf-8'))
    refs = generate_references(dataset)
    active_root = ds.storage()
    active = active_root / 'references.json'
    original_bytes = active.read_bytes()
    original = ds._load()
    if len(original['references']) != 4:
        raise ValueError('Se esperan las cuatro referencias originales; no se sobrescribe otro estado.')
    digest_before = hashlib.sha256(original_bytes).hexdigest()
    backup = active_root / ('references.before-concierge-' + digest_before[:16] + '.json')
    if not backup.exists():
        with backup.open('xb') as stream:
            stream.write(original_bytes)
    if backup.read_bytes() != original_bytes:
        raise ValueError('El respaldo no coincide.')
    candidate_root = active_root / ('candidate-concierge-' + secrets.token_hex(5))
    candidate_root.mkdir()
    env_before = os.environ.get('PROYECTO_DOCUMENT_REFERENCES_DIR')
    os.environ['PROYECTO_DOCUMENT_REFERENCES_DIR'] = str(candidate_root)
    start = time.perf_counter()
    try:
        candidate = copy.deepcopy(original)
        candidate.pop('calibration', None)
        reused = None
        for previous in sorted(active_root.glob('candidate-concierge-*/references.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            stored = json.loads(previous.read_text(encoding='utf-8'))
            if (stored.get('extension', {}).get('original_cache_sha256') == digest_before
                    and stored.get('digest') == original['digest']):
                reused = {r['sha256']: r for r in stored['references'][4:]}
                break
        for item, path, extracted in refs:
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            saved = (reused or {}).get(sha)
            candidate['references'].append(copy.deepcopy(saved) if saved else {'name': path.name, 'sha256': sha,
                'vectors': ds.embed(ds.chunks(extracted)), 'synthetic': True, 'document_type': item['document_type']})
            print(('Vector reutilizado: ' if saved else 'Referencia vectorizada: ') + path.name, flush=True)
        candidate['extension'] = {'dataset': dataset['version'], 'original_cache_sha256': digest_before,
                                  'original_reference_count': 4}
        ds._save(candidate)
        old_examples = json.loads((FIXTURES / 'document_similarity_calibration.json').read_text(encoding='utf-8'))
        examples = old_examples + [{**e, 'text': dataset['notice'] + '\n' + e['text']} for e in dataset['calibration']]
        # Independencia exacta de textos antes de observar puntuaciones.
        ref_texts = {re.sub(r'\s+', ' ', dataset['notice'] + '\n' + e['text']).strip() for e in dataset['references']}
        cal_texts = {re.sub(r'\s+', ' ', e['text']).strip() for e in examples}
        val_texts = {re.sub(r'\s+', ' ', dataset['notice'] + '\n' + e['text']).strip() for e in dataset['validation']}
        if ref_texts & cal_texts or ref_texts & val_texts or cal_texts & val_texts:
            raise ValueError('Referencias, calibración y validación deben ser independientes.')
        print('Calibrando ejemplos positivos y negativos; aún no se evalúa el lote final.', flush=True)
        calibration = ds.calibrate(examples, allow_overlap=True)
        validated = []
        for example in dataset['validation']:
            text = dataset['notice'] + '\n' + example['text']
            result = evaluate_candidate(text, [{'text': text, 'source': 'Pagina 1'}], candidate['references'])
            validated.append({'id': example['id'], 'expected': example['expected'], **result})
            print(example['id'] + ': ' + result['decision'], flush=True)
        candidate = ds._load()
        preserved = candidate['references'][:4] == original['references']
        safety = (preserved and all(r['decision'] in r['expected'] for r in validated if not r['id'].startswith('val_limite'))
                  and all(r['decision'] in {'rechazado', 'indeterminado'} for r in validated if r['id'].startswith('val_limite')))
        report = {'dataset': dataset['version'], 'model': ds.MODEL, 'model_digest': candidate['digest'],
                  'algorithm': ds.VERSION, 'original_references_preserved': preserved,
                  'reference_metadata': [{k: v for k, v in r.items() if k != 'vectors'} for r in candidate['references']],
                  'previous_thresholds': {k: original['calibration'][k] for k in ('accept', 'reject')},
                  'calibration': calibration, 'validation': validated, 'safety_checks_passed': safety,
                  'boundary_expectations_met': all(r['decision'] in r['expected'] for r in validated if r['id'].startswith('val_limite')),
                  'seconds': round(time.perf_counter() - start, 3), 'applied': False}
        if REPORT.exists():
            prior = json.loads(REPORT.read_text(encoding='utf-8'))
            if prior['dataset'] != dataset['version']:
                archive = REPORT.with_name('document_similarity_concierge_results_initial.json')
                if not archive.exists():
                    archive.write_bytes(REPORT.read_bytes())
                # Con los mismos vectores, reclasificar puntuaciones ya observadas
                # no requiere otra inferencia ni altera la calibracion.
                if prior['model_digest'] == candidate['digest'] and prior['reference_metadata'] == report['reference_metadata']:
                    diagnostics = []
                    for row in prior['validation']:
                        best = max(row['similarities'].values())
                        decision = 'aceptado' if best >= calibration['accept'] else 'rechazado' if best <= calibration['reject'] else 'indeterminado'
                        diagnostics.append({'id': row['id'], 'best': best, 'decision': decision})
                    report['previous_diagnostic_reclassified'] = diagnostics
                    if any(r['decision'] == 'aceptado' for r in diagnostics if r['id'].startswith('val_limite')):
                        safety = False
                        report['safety_checks_passed'] = False
        if active.read_bytes() != original_bytes or ds.model_digest() != original['digest']:
            raise ValueError('El caché activo o el modelo cambiaron; no se aplica.')
        if apply and safety:
            os.environ['PROYECTO_DOCUMENT_REFERENCES_DIR'] = str(active_root)
            ds._save(candidate)
            report['applied'] = True
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(REPORT), 'thresholds': {k: calibration[k] for k in ('accept', 'reject')},
                          'preserved': preserved, 'applied': report['applied'], 'safety_checks_passed': safety}), flush=True)
        return report
    finally:
        if env_before is None:
            os.environ.pop('PROYECTO_DOCUMENT_REFERENCES_DIR', None)
        else:
            os.environ['PROYECTO_DOCUMENT_REFERENCES_DIR'] = env_before


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aplicar', action='store_true')
    args = parser.parse_args()
    result = prepare(args.aplicar)
    if args.aplicar and not result['applied']:
        raise SystemExit(2)  # preparacion disponible, pero activacion no autorizada por los controles
