"""Recursos documentales de las sesiones locales existentes; sin otra identidad.

Solo metadatos/hashes en identities.json. Los vectores permanecen en la sesion.
El usuario de Windows que administra el almacen sigue siendo frontera confiable.
Originales temporales: cierre, expiracion y limpiar chat revocan su acceso.
Limpieza oportunista (hasta 64 intentos por llamada), sin servicios nuevos:
si la aplicacion esta apagada no se borra hasta la siguiente llamada.
Huerfanos sin registro no se eliminan automaticamente: requieren revision local
de su pertenencia y de las sesiones validas antes de cualquier borrado manual.
No se modifica el almacen independiente de referencias/embeddings.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import logging
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar

from proyecto.security import local_auth as auth
_IO_HELD = ContextVar('document_io_held', default=False)


@contextmanager
def _document_io(reentrant=True):
    """Bloqueo entre procesos: no eliminar durante una extraccion activa."""
    if reentrant and _IO_HELD.get():
        yield
        return
    with (auth.ROOT / 'document_io.lock').open('a+b') as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = _IO_HELD.set(True)
        try:
            yield
        finally:
            _IO_HELD.reset(active)
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _registered_path(identifier, entry):
    """Solo un archivo registrado; nunca enlaces, junctions ni rutas externas."""
    if not re.fullmatch(r'doc_[0-9a-f]{48}', identifier):
        raise PermissionError('Identificador documental invalido.')
    scope = entry.get('binding', '')
    if not re.fullmatch(r'[0-9a-f]{64}', scope):
        raise PermissionError('Alcance documental invalido.')
    root = auth.ROOT.absolute()
    path = Path(entry['path'])
    expected = root / 'documents' / scope / path.name
    if (not path.is_absolute() or path != expected or path.stem != identifier
            or path.suffix.lower() not in {'.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.csv', '.md', '.html', '.htm', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}):
        raise PermissionError('Ruta documental no registrada.')
    for part in (root, root / 'documents', expected.parent, expected):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise PermissionError('Enlace documental no permitido.')
    return expected


def _cleanup(data, limit=64):
    """Se llama bajo el bloqueo del registro. Fallos conservan su entrada."""
    result = {'deleted': 0, 'pending': 0}
    remaining = limit
    for session in data['sessions'].values():
        expired = session.get('expires', 0) <= time.time()
        for identifier, entry in list(session.get('documents', {}).items()):
            if not (expired or entry.get('delete_pending')):
                continue
            entry['delete_pending'] = True
            entry.pop('context_sha256', None)
            if remaining <= 0:
                result['pending'] += 1
                continue
            remaining -= 1
            try:
                path = _registered_path(identifier, entry)
                with _document_io(reentrant=False):
                    # Revalidar tras adquirir el bloqueo compartido con extraccion.
                    path = _registered_path(identifier, entry)
                    if path.exists():
                        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                            raise PermissionError('Archivo cambiado; requiere revision local.')
                        path.unlink()
                    session['documents'].pop(identifier)
                    result['deleted'] += 1
            except (OSError, ValueError, KeyError):
                entry['cleanup_error'] = 'No eliminado; reintento pendiente.'
                logging.getLogger(__name__).warning('Limpieza documental pendiente; archivo no eliminado.')
                result['pending'] += 1
    return result


@auth._serialized
def cleanup_expired():
    """Mantenimiento oportunista y acotado; no explora ni borra huerfanos."""
    data = auth._read()
    result = _cleanup(data)
    auth._write(data)
    return result


@auth._serialized
def clear_session_documents():
    data = auth._read()
    session = _session(data)
    for entry in session.get('documents', {}).values():
        entry['delete_pending'] = True
    result = _cleanup(data)
    auth._write(data)
    return result


def binding():
    user = auth.principal()
    token = auth.TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    fields = [token, user['subject'], user['role'], user['database'], user['digest'], user['customers'], user['permissions']]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _session(data):
    auth.principal()  # expiracion, revocacion y base: autoridad existente
    token = auth.TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    return data['sessions'][hashlib.sha256(token.encode()).hexdigest()]


def _entry(data, reference):
    scope = binding()
    documents = _session(data).get('documents', {})
    found = next(((key, value) for key, value in documents.items()
                  if reference in {key, value['path']}), None)
    if not found or found[1]['binding'] != scope or found[1].get('delete_pending'):
        raise PermissionError('Documento no autorizado para la sesion y alcance actuales.')
    return found


@auth._serialized
def store_upload(content: bytes, suffix: str, name: str) -> str:
    """Solo bytes recibidos por el servidor; nunca registra una ruta del cliente."""
    scope = binding()
    from proyecto.agents.document_intelligence_agent import EXTENSIONES_DOCUMENTO, EXTENSIONES_IMAGEN, MAX_DOCUMENT_BYTES, MAX_IMAGE_BYTES
    limit = MAX_IMAGE_BYTES if suffix.lower() in EXTENSIONES_IMAGEN else MAX_DOCUMENT_BYTES
    if suffix.lower() not in EXTENSIONES_DOCUMENTO | EXTENSIONES_IMAGEN or len(content) > limit:
        raise ValueError('Formato o tamano documental no permitido.')
    data = auth._read()
    session = _session(data)
    identifier = 'doc_' + secrets.token_hex(24)
    root = auth.ROOT.absolute() / 'documents' / scope
    _registered_path(identifier, {'path': str(root / (identifier + suffix.lower())), 'binding': scope})
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (identifier + suffix.lower())
    with path.open('xb') as stream:
        stream.write(content)
    session.setdefault('documents', {})[identifier] = {
        'path': str(path), 'binding': scope, 'name': Path(name).name,
        'sha256': hashlib.sha256(content).hexdigest(),
    }
    auth._write(data)
    return identifier


@auth._serialized
def authorize_file(reference: str) -> tuple[str, dict]:
    # Verificar el registro ANTES de stat/open/read del documento.
    data = auth._read()
    identifier, entry = _entry(data, reference)
    path = _registered_path(identifier, entry)
    root = (auth.ROOT / 'documents' / entry['binding']).resolve()
    if not path.resolve().is_relative_to(root):
        raise PermissionError('Ruta documental fuera del almacen autorizado.')
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        raise PermissionError('El archivo autorizado cambio; vuelve a subirlo.')
    return identifier, dict(entry)


def authorize_content(text: str):
    """Control previo a embeddings de TODAS las representaciones extraidas.

    Identificadores protegidos sin titularidad comprobable se deniegan, no se
    asumen propios por provenir de una subida. No interpreta nombres como IDs.
    """
    auth.principal()
    import unicodedata
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    normalized = re.sub(r'\b(?:num_cte|id_cliente|cliente_id)\b', 'cliente', normalized)
    normalized = re.sub(r'\b(?:num_cta|id_cuenta|cuenta_id)\b', 'cuenta', normalized)
    for match in re.finditer(r'\b(cuenta|cliente)(?=[ \t:\n|\d*x#-]|$)([ \t]*[:|]?)[ \t]*', normalized):
        value = re.split(r'[\n|;]', normalized[match.end():], maxsplit=1)[0].strip()
        line_prefix = normalized[:match.start()].rsplit('\n', 1)[-1].strip(' #*_`|-')
        if not value and line_prefix in {'', 'numero de', 'no. de'}:
            raise PermissionError('Identificador protegido sin limites verificables.')
        prefixed = bool(re.match(r'^(?:numero|num\.?|nro\.?|no\.?|n[°º])[ \t:]', value))
        if prefixed:
            value = re.sub(r'^(?:numero|num\.?|nro\.?|no\.?|n[°º])[ \t:]*', '', value)
        if re.match(r'^\d+\b', value):
            number = re.match(r'\d+', value)[0]
            auth.authorize_request(f'{match[1]} {number}')
        elif (prefixed or any(c in match[2] for c in ':|') or re.match(r'^(?:[*x#-]+|[a-z]+[-_]*\d|demo[-_])', value)):
            raise PermissionError('Titularidad de un identificador protegido no verificable.')


def extract_authorized(reference: str, modo: str = 'auto', paginas: str | None = None):
    """Autoriza archivo, valida, extrae y controla datos antes de devolver texto."""
    auth.principal()
    with _document_io():
        return _extract_authorized(reference, modo, paginas)


def _extract_authorized(reference: str, modo: str = 'auto', paginas: str | None = None):
    identifier, entry, text, pages, incomplete = _read_authorized(reference, modo, paginas)
    from proyecto.agents.document_questions import _sanitize
    permissions = auth.principal()['permissions']
    safe_text = _sanitize(text, permissions)
    for page in pages:
        page['text'] = _sanitize(page['text'], permissions)
    authorize_file(identifier)
    return identifier, entry, safe_text, pages, incomplete


def extract_requirement_metadata(reference: str):
    """Solo metadatos mínimos de un archivo propio; sin OCR generativo ni PII de salida.

    Diagnóstico acotado de reglas; no concede admisión semántica o acceso al LLM.
    """
    auth.principal()
    with _document_io():
        identifier, _, _, pages, incomplete = _read_authorized(reference, 'texto', allow_unreadable=True)
        from proyecto.agents.document_requirements import requirement_metadata
        result = requirement_metadata(pages, incomplete)
        authorize_file(identifier)
        return identifier, result


def _read_authorized(reference, modo='auto', paginas=None, *, allow_unreadable=False):
    identifier, entry = authorize_file(reference)
    from proyecto.agents.document_intelligence_agent import _resolver_documento, _convertir_documento
    from proyecto.agents.document_questions import extract_pages
    from proyecto.agents.document_similarity import SimilarityError
    path = _resolver_documento(entry['path'])
    text = (_convertir_documento(path) if modo == 'auto' and not paginas else
            _convertir_documento(path, modo=modo, paginas=paginas))
    if allow_unreadable and (not text.strip() or text.startswith('MarkItDown no extrajo')):
        authorize_file(identifier)
        return identifier, entry, '', [], True
    if text.startswith(('No se pudo', 'MarkItDown no extrajo')) or '[Contenido truncado:' in text:
        raise SimilarityError('Extraccion documental incompleta.')
    try:
        pages, incomplete = extract_pages(path, text)
    except SimilarityError as error:
        if allow_unreadable and str(error).startswith('No se extrajo texto localizable'):
            pages, incomplete = [], True
        else:
            raise
    for representation in [text, *(page['text'] for page in pages)]:
        authorize_content(representation)
    authorize_file(identifier)  # detectar sustitucion durante la extraccion
    return identifier, entry, text, pages, incomplete


def _digest(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def bind_context(reference: str, context: dict):
    authorize_file(reference)
    _bind_context(reference, context)


@auth._serialized
def _bind_context(reference: str, context: dict):
    data = auth._read()
    _, entry = _entry(data, reference)
    entry['context_sha256'] = _digest(context)
    auth._write(data)


def authorize_context(context: dict):
    reference = context.get('document_id', '')
    authorize_file(reference)
    data = auth._read()
    _, entry = _entry(data, reference)
    if entry.get('context_sha256') != _digest(context):
        raise PermissionError('Contexto documental alterado o no autorizado.')


def invalidate_visible_state(state):
    """Antes de mostrar cualquier historial/artefacto de una sesion documental."""
    context = state.get('documento_consultable')
    if not state.get('document_history_binding') and not context:
        return
    try:
        current = binding()
        if state.get('document_history_binding') != current:
            raise PermissionError()
        for reference in state.get('history_document_ids', []):
            authorize_file(reference)
        if context:
            authorize_context(context['index'])
    except Exception:
        # Incluye referencias en otros mensajes: no conservar restos de contexto.
        for key in ('mensajes', 'artefacto', 'documento_consultable', 'document_history_binding',
                    'history_document_ids',
                    'orquestador_sesion', 'archivo_actual', 'archivo_nombre_actual', 'archivo_tipo', 'consulta_actual'):
            state.pop(key, None)
        state['pensando'] = False
