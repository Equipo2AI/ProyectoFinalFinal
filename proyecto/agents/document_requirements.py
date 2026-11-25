"""Reglas de expediente dentro de Document Intelligence; sin LLM ni almacén.

Solo se publican tipo, presencia de campos, fechas necesarias y ubicaciones.
No se comparan nombres, CURP o domicilios ni se infiere identidad/titularidad.
"""
from __future__ import annotations

import calendar
from datetime import date
import json
from pathlib import Path
import re

from proyecto.agents.document_questions import normalize, _explicit_date

_MATRIX = Path(__file__).resolve().parents[1] / 'config' / 'micuenta_requirements.json'


def requirements_matrix():
    """Fuente fija del servidor; nunca acepta políticas aportadas por el chat."""
    matrix = json.loads(_MATRIX.read_text(encoding='utf-8'))
    conditions = {'ine': 'siempre', 'domicilio': 'siempre', 'curp': 'ine_no_incluye_curp', 'rfc': 'actividad_empresarial'}
    if (len(matrix['requisitos']) != 4 or {r['id']: r['condicion'] for r in matrix['requisitos']} != conditions
            or matrix['perfil_habilitado'] != {'persona': 'fisica', 'nacionalidad': 'mexicana', 'actividad_empresarial': False}):
        raise ValueError('Matriz fuera del perfil habilitado.')
    return matrix


def _presence(lines, field):
    """Tres valores: dato extraído, ausencia explícita o no verificable."""
    values = []
    for line in lines:
        match = re.fullmatch(r'\s*(?:' + field + r')\s*:\s*(.*?)\s*', line)
        if match:
            value = match[1].strip()
            if value in {'no', 'no incluida', 'no incluido', 'no consta', 'no incluye', 'ausente'}:
                values.append(False)
            elif not value or value in {'si', 'presente', 'incluida', 'incluido', 'n/a', 'sin dato', 'sin datos'} or re.search(r'ilegible|redactad|no disponible|no verificable|\*{3,}|\[.*\]', value):
                values.append(None)
            else:
                values.append(True)
    if not values or None in values or len(set(values)) != 1:
        return None
    return values[0]


def _date_fact(lines, label):
    values = []
    for line in lines:
        match = re.fullmatch(r'\s*(?:' + label + r')\s*:\s*(.*?)\s*', line)
        if match:
            values.append(match[1])
    if len(set(values)) != 1:
        return None
    value = _explicit_date(values[0])
    return value.isoformat() if value else None


def requirement_metadata(pages, incomplete):
    """Llamar solo sobre extracción ya autorizada, antes de descartar PII.

    Los valores de identificación nunca abandonan esta función; las páginas
    son ordinales, no encabezados o nombres de archivo del usuario.
    """
    lines = [normalize(line).strip(' #*_') for p in pages for line in p['text'].splitlines()]
    # Reconocimiento de tipo separado de los hechos usados por la matriz.
    # Segmentar frases explícitas permite campos en párrafos sin inferir valores.
    clauses = [c.strip(' #*_').rstrip('.') for line in lines for c in re.split(r';\s*|\.\s+(?=[a-z])', line)]
    body = '\n'.join(clauses)
    claims = []
    type_patterns = [
        ('ine', r'\b(?:credencial para votar|identificacion (?:oficial )?ine)\b'),
        ('curp', r'\b(?:constancia (?:de )?curp|constancia de clave unica de registro de poblacion)\b'),
        ('domicilio', r'\b(?:comprobante (?:de domicilio|residencial)|recibo de (?:luz|energia electrica|telefonia fija|predial|television de paga))\b'),
    ]
    for candidate, pattern in type_patterns:
        if re.search(pattern, body):
            claims.append(candidate)
    purpose_uncertain = bool(re.search(
        r'\b(?:lista de requisitos|se (?:piden|requieren)|formulario|plantilla|'
        r'como seria|representa una|representacion textual|incluye un espacio|'
        r'instrucciones|guia para|receta|sin completar)\b', body))
    # Los campos deben pertenecer al documento presentado, no a un tema
    # mencionado en un aviso o artículo. La marca de simulación no acredita
    # el tipo, ni invalida por sí misma un documento sintético estructurado.
    explanatory = bool(re.search(
        r'\b(?:aviso de privacidad|articulo (?:educativo|informativo)|'
        r'nota informativa|ejemplo de (?:campos|requisitos)|'
        r'no (?:es|constituye|representa) (?:una? )?(?:constancia|credencial|comprobante)|'
        r'(?:explicar|describe|describir) (?:los |sus )?(?:campos|requisitos))\b', body))
    purpose_uncertain = purpose_uncertain or explanatory
    # Un registro pertenece a un bloque de una página. No sumar campos de
    # otras páginas, secciones o registros a un encabezado aislado.
    blocks = []
    for page in pages:
        for block in re.split(r'\n\s*\n|\n(?=\s*#{1,6}\s)', normalize(page['text'])):
            local = [c.strip(' #*_').rstrip('.') for line in block.splitlines()
                     if line.strip() and not line.startswith('simulacion - solo pruebas')
                     for c in re.split(r';\s*|\.\s+(?=[a-z])', line)]
            if local:
                blocks.append(local)
    local = blocks[0] if blocks else []
    heading = local[0] if local else ''
    # Se consume TODO el encabezado: un prefijo "Constancia CURP" no acredita
    # una "Constancia CURP: lectura comentada". Calificadores desconocidos
    # conservan incertidumbre, no se suprimen para hacer coincidir el título.
    qualifier = (r'(?:\s*[-:|()]?\s*(?:simulacion|documento sintetico|sintetic[oa]|'
                 r'simulad[oa]|solo pruebas|sin validez oficial|de prueba|de demostracion))*\s*[-.)]*')
    presented_types = []
    for candidate, pattern in type_patterns:
        match = re.match(pattern, heading)
        if match and re.fullmatch(qualifier, heading[match.end():]):
            presented_types.append(candidate)
    record_headers = sum(bool(re.match(pattern, block[0])) for block in blocks for _, pattern in type_patterns)
    purpose_uncertain = purpose_uncertain or bool(re.search(
        r'\b(?:material (?:de estudio|educativo|didactico)|lectura comentada|'
        r'ejercicio (?:resuelto|didactico)|modelo de (?:constancia|credencial|comprobante))\b', body))
    # Una reproducción de registro dentro de un contexto docente sigue siendo
    # material explicativo, aunque el registro reproducido esté completo.
    purpose_uncertain = purpose_uncertain or bool(
        re.search(r'\b(?:curso|seminario|leccion|clase|alumnado)\b', body)
        and re.search(r'\b(?:transcripcion|reproduccion|reproduce|campos|apartados)\b', body))
    person = _presence(local, 'persona|persona registrada|titular|nombre') is True
    population = _presence(local, 'curp|valor de la curp') is True
    address = _presence(local, 'domicilio|direccion|domicilio del servicio|domicilio de instalacion') is True
    record = _presence(local, 'numero de registro|registro|clave de elector|numero de credencial') is True
    expiry = _date_fact(local, 'vigente hasta|fecha de vencimiento|vencimiento|vigencia|fecha final de vigencia declarada')
    issued = _date_fact(local, 'fecha de emision|emision|fecha de expedicion|fecha de emision ilustrativa')
    service = bool(re.search(r'\b(?:recibo de (?:luz|energia electrica|telefonia fija|predial|television de paga)|tipo de recibo: (?:luz|telefonia fija|predial|television de paga))\b', '\n'.join(local)))
    specific = {'ine': person and record and expiry is not None,
                'curp': person and population and (_presence(local, 'entidad de registro|entidad registradora') is True or issued is not None),
                'domicilio': person and address and service and issued is not None}
    # El tipo no exige todos los campos cuyo cumplimiento se evalúa después.
    type_evidence = {
        'ine': person and record,
        'curp': person and (_presence(local, 'entidad de registro|entidad registradora') is True or issued is not None),
        'domicilio': person and service and (address or issued is not None),
    }
    recognized = claims[0] if len(claims) == 1 else None
    individual_document = (recognized is not None and presented_types == [recognized]
                           and record_headers == 1 and not purpose_uncertain)
    type_recognizable = bool(individual_document and type_evidence.get(recognized) and not incomplete)
    local_receipts = set()
    for label, pattern in [('luz', r'\b(?:recibo de (?:luz|energia electrica)|tipo de recibo: luz)\b'),
                           ('telefonia_fija', r'\btelefonia fija\b'), ('predial', r'\bpredial\b'),
                           ('television_paga', r'\b(?:television de paga|tv de paga)\b')]:
        if re.search(pattern, '\n'.join(local)):
            local_receipts.add(label)
    return {'type': recognized if type_recognizable else None, 'readable': bool(pages) and not incomplete,
            'recognition': {'type': recognized, 'multiple_types': len(claims) > 1,
                            'purpose_uncertain': purpose_uncertain,
                            'individual_document': individual_document,
                            'single_record_block': record_headers == 1,
                            'full_heading_matches': presented_types == [recognized] if recognized else False,
                            'specific_fields': bool(specific.get(recognized)),
                            'type_evidence_sufficient': bool(type_evidence.get(recognized)),
                            'type_recognizable': type_recognizable,
                            'sufficient': bool(individual_document and specific.get(recognized) and not incomplete)},
            'curp_present': _presence(local, 'curp|valor de la curp'),
            'address_present': _presence(local, 'domicilio|direccion|domicilio del servicio|domicilio de instalacion'),
            'expiry': expiry, 'issued': issued,
            'receipt_type': next(iter(local_receipts)) if len(local_receipts) == 1 else None,
            'pages': [int(m[1]) for p in pages if (m := re.match(r'^pagina\s+(\d+)\b', normalize(p['source'])))],
            'sections': [int(m[1]) for p in pages if (m := re.match(r'^seccion\s+(\d+)\b', normalize(p['source'])))]}


def _months_before(day, months):
    total = day.year * 12 + day.month - 1 - months
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, min(day.day, calendar.monthrange(year, month0 + 1)[1]))


def evaluate_requirement_metadata(documents, *, today=None):
    """Función interna para metadatos del servidor, no herramienta MCP pública."""
    today = today or date.today()
    matrix = requirements_matrix()
    results = []
    unidentified = any(d['metadata']['type'] is None for d in documents)
    ids = [d for d in documents if d['metadata']['type'] == 'ine']
    ine_curp = (ids[0]['metadata']['curp_present'] if len(ids) == 1 and ids[0]['metadata']['readable'] else None)
    for rule in matrix['requisitos']:
        key = rule['id']
        condition = rule['condicion']
        applies = (True if condition == 'siempre' else matrix['perfil_habilitado']['actividad_empresarial']
                   if condition == 'actividad_empresarial' else not ine_curp if ine_curp is not None else None)
        row = {'requisito': key, 'aplica': applies, 'estado': None, 'cumple': None,
               'incidencias': [], 'evidencias': [], 'fuente': rule['fuente']}
        results.append(row)
        if applies is False:
            row['explicacion'] = 'No aplica al perfil o la condición documental comprobada.'
            continue
        candidates = [d for d in documents if d['metadata']['type'] == rule['document_type']]
        if applies is None:
            row.update(estado='NO_VERIFICABLE', explicacion='No se puede determinar si la INE incluye CURP; la constancia separada no se exige por inferencia.')
            continue
        if not candidates:
            row.update(estado='NO_VERIFICABLE' if unidentified else 'FALTANTE',
                       cumple=None if unidentified else False,
                       explicacion='Hay archivos cuyo tipo no pudo identificarse.' if unidentified else 'No se presentó el documento requerido.')
            continue
        row['evidencias'] = [{'documento_id': d['documento_id'], 'paginas': d['metadata']['pages'],
                             'secciones': d['metadata']['sections']} for d in candidates]
        if len(candidates) != 1:
            row.update(estado='NO_VERIFICABLE', explicacion='Hay varias versiones; selecciona el documento que debe evaluarse.')
            continue
        meta = candidates[0]['metadata']
        row['estado'] = 'PRESENTE'
        if not meta['readable']:
            row.update(estado='NO_VERIFICABLE', explicacion='Extracción incompleta o campos no legibles.')
            continue
        if key == 'ine':
            expiry = _explicit_date(meta['expiry']) if meta['expiry'] else None
            if not expiry:
                row.update(explicacion='Identificación presente; no se pudo comprobar una fecha completa de vencimiento.',
                           campos_no_verificables=['vigencia'])
                row['incidencias'].append({'estado': 'NO_VERIFICABLE', 'campo': 'vigencia'})
                continue
            if expiry < today:
                row.update(cumple=False, explicacion='La fecha de vencimiento declarada ya pasó.')
                row['incidencias'].append({'estado': 'VENCIDO', 'campo': 'vigencia'})
        elif key == 'domicilio':
            issued = _explicit_date(meta['issued']) if meta['issued'] else None
            if meta['receipt_type'] not in rule['tipos_recibo'] or meta['address_present'] is not True or not issued:
                fields = ['tipo_recibo'] if meta['receipt_type'] not in rule['tipos_recibo'] else []
                fields += ['domicilio'] if meta['address_present'] is not True else []
                fields += ['emision'] if not issued else []
                row.update(explicacion='Comprobante presente; no se pudieron verificar todos los campos requeridos.',
                           campos_no_verificables=fields)
                row['incidencias'].extend({'estado': 'NO_VERIFICABLE', 'campo': field} for field in fields)
                if meta['address_present'] is False:
                    row['cumple'] = False
                    row['incidencias'].append({'estado': 'AUSENTE', 'campo': 'domicilio'})
                continue
            if issued > today or issued < _months_before(today, rule['antiguedad_maxima_meses']):
                row.update(cumple=False, explicacion='La emisión es futura o excede la antigüedad máxima publicada.')
                row['incidencias'].append({'estado': 'INCONSISTENTE' if issued > today else 'ANTIGUEDAD_EXCEDIDA', 'campo': 'emision'})
        elif key == 'curp' and meta['curp_present'] is not True:
            row.update(explicacion='Constancia presente; el valor CURP está ausente o no pudo extraerse. No se infiere ni se publica.',
                       campos_no_verificables=['curp'])
            row['incidencias'].append({'estado': 'AUSENTE' if meta['curp_present'] is False else 'NO_VERIFICABLE', 'campo': 'curp'})
            if meta['curp_present'] is False:
                row['cumple'] = False
            continue
        if row['cumple'] is None:
            row.update(estado='VALIDO_SEGUN_REGLAS', cumple=True, explicacion='Cumple únicamente las reglas documentales declaradas y verificables de esta etapa.')
    complete = all(r['aplica'] is False or r['cumple'] is True for r in results)
    return {'code': 'OK', 'matriz': matrix, 'fecha_evaluacion': today.isoformat(), 'requisitos': results,
            'completo_segun_reglas': complete, 'aprobacion_bancaria': None, 'titularidad_verificada': False,
            'admision_semantica': 'NO_EVALUADA_EN_ESTA_ETAPA', 'analisis_llm_habilitado': False,
            'aviso': 'Completo según estas reglas no significa autenticidad, titularidad comprobada ni aprobación bancaria.'}


def evaluate_case(references):
    from proyecto.security.document_access import authorize_file, extract_requirement_metadata
    from proyecto.security.local_auth import principal
    principal()
    references = list(dict.fromkeys(references))
    if len(references) > 8:
        raise ValueError('Límite técnico: ocho documentos por evaluación.')
    # Autorizar todo el lote antes de leer cualquiera de sus archivos.
    for reference in references:
        authorize_file(reference)
    documents = []
    admissions = []
    for reference in references:
        identifier, meta = extract_requirement_metadata(reference)
        recognized = meta.get('recognition', {}).get('type_recognizable', False)
        admissions.append({'documento_id': identifier, 'decision': 'reconocido' if recognized else 'indeterminado',
                           'metodo': 'perfil_documental_acotado', 'admision_semantica': 'NO_EVALUADA'})
        if not recognized:
            meta = {'type': None, 'readable': False}
        documents.append({'documento_id': identifier, 'metadata': meta})
    result = evaluate_requirement_metadata(documents)
    result['admision_semantica'] = admissions
    for reference in references:
        authorize_file(reference)
    return result
