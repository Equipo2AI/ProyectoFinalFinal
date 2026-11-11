"""Identidades de desarrollo aprovisionadas por el operador, nunca por el chat.

Los archivos privados viven fuera de Git. El mismo usuario de Windows que
administra esos archivos es una frontera de confianza; no es autenticacion web
de produccion. Las sesiones son opacas, revocables y ligadas a una base concreta.
"""
import argparse
from contextvars import ContextVar
from contextlib import contextmanager, closing
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from functools import wraps
from threading import RLock

_MUTATIONS = RLock()

def _serialized(func):
    @wraps(func)
    def run(*args, **kwargs):
        with _MUTATIONS:
            ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (ROOT / 'identities.lock').open('a+b') as lock:
                if lock.seek(0, os.SEEK_END) == 0:
                    lock.write(b'0')
                    lock.flush()
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    return func(*args, **kwargs)
                finally:
                    lock.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return run

LEGACY_ROOT = Path(__file__).resolve().parents[2] / '.local_auth'


def auth_directory():
    """CLI, Streamlit y MCP comparten un almacen privado por checkout y usuario."""
    override = os.environ.get('PROYECTO_AUTH_DIR')
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get('LOCALAPPDATA')
    if os.name == 'nt' and not local:
        raise RuntimeError('LOCALAPPDATA no esta definido; inicia con tu usuario de Windows.')
    base = Path(local) if local else Path.home() / '.local' / 'share'
    project = hashlib.sha256(str(LEGACY_ROOT.parent).casefold().encode()).hexdigest()[:12]
    return base / 'ProyectoFinalFinal' / 'auth' / project


ROOT = auth_directory()
TOKEN = ContextVar('principal_token', default='')
STATE = ROOT / 'identities.json'
DEMO_SUBJECT = 'demo_superuser'
DEMO_PERMISSIONS = ['aggregate', 'audit', 'governance', 'pii:nombre', 'pii:rfc']


def auth_mode():
    """El modo visual demo no concede identidad ni permisos de datos."""
    return (os.environ.get('PROYECTO_AUTH_MODE', 'local').strip().lower() or 'local')


def es_modo_demo():
    return auth_mode() == 'demo' and not (
        TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    )


def _read():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'users': {}, 'sessions': {}}


def _write(data):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(STATE)


@_serialized
def migrate_identities(source):
    """Importacion explicita, sin borrar origen ni sobrescribir identidades.

    Solo se conservan hashes y permisos existentes; nunca sesiones antiguas.
    """
    try:
        incoming = json.loads(Path(source).read_text(encoding='utf-8'))['users']
    except PermissionError as error:
        raise PermissionError('No se puede leer el origen de migracion con este usuario. No se cambian sus permisos.') from error
    if not isinstance(incoming, dict):
        raise ValueError('Registro de identidades invalido.')
    data = _read()
    for name, user in incoming.items():
        if (not isinstance(user, dict) or user.get('role') not in {'normal','auditor'}
                or not isinstance(user.get('customers'), list)
                or not isinstance(user.get('permissions'), list)
                or not all(k in user for k in ('salt','password','database','digest'))):
            raise ValueError('Identidad de origen fuera de contrato; no se importa.')
        if name in data['users'] and data['users'][name] != user:
            raise ValueError('Hay identidades diferentes con el mismo nombre; no se sobrescribe el destino.')
    added = sum(name not in data['users'] for name in incoming)
    data['users'].update(incoming)
    _write(data)
    return added


def db_binding():
    from sqlalchemy.engine import make_url
    from proyecto.agents.database import get_database_url
    url = make_url(get_database_url())
    if url.drivername != 'sqlite':
        raise PermissionError('Este mecanismo local solo admite SQLite.')
    path = Path(url.database).resolve()
    if not path.is_file():
        raise PermissionError('Base autorizada no disponible.')
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def _demo_principal(permission=None):
    """Compatibilidad: la vista demo nunca suplanta un auditor autenticado."""
    raise PermissionError('Acceso denegado: inicia sesion con una identidad aprovisionada.')


@_serialized
def provision(username, password, role, customers, permissions):
    """Solo operador local. Verifica asignaciones contra clientes existentes."""
    import sqlite3
    if role not in {'normal', 'auditor'} or len(password) < 16:
        raise ValueError('Rol invalido o clave menor de 16 caracteres.')
    scope = sorted(set(int(c) for c in customers))
    if not scope or (role == 'normal' and (len(scope) != 1 or permissions)):
        raise ValueError('Un usuario normal se vincula a un cliente, sin privilegios.')
    allowed = {'aggregate', 'audit', 'governance', 'pii:nombre', 'pii:rfc'}
    if not set(permissions) <= allowed:
        raise ValueError('Permiso no reconocido.')
    path, digest = db_binding()
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as c:
        existing = {r[0] for r in c.execute('SELECT num_cte FROM dim_cliente')}
    if not set(scope) <= existing:
        raise ValueError('El alcance contiene clientes inexistentes.')
    data = _read()
    if username in data['users']:
        raise ValueError('La identidad ya existe; no se sobrescribe.')
    salt = secrets.token_hex(16)
    data['users'][username] = {
        'salt': salt, 'password': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000).hex(),
        'role': role, 'customers': scope, 'permissions': sorted(set(permissions)),
        'database': path, 'digest': digest,
    }
    _write(data)


@_serialized
def login(username, password):
    data = _read()
    user = data['users'].get(username)
    salt = bytes.fromhex(user['salt']) if user else bytes(16)
    computed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000).hex()
    if not user or not hmac.compare_digest(user['password'], computed):
        raise PermissionError('Credenciales invalidas.')
    token = secrets.token_urlsafe(32)
    data['sessions'][hashlib.sha256(token.encode()).hexdigest()] = {'user': username, 'expires': time.time() + 3600}
    _write(data)
    return token


@_serialized
def logout(token):
    data = _read()
    data['sessions'].pop(hashlib.sha256(token.encode()).hexdigest(), None)
    _write(data)


def principal(permission=None):
    token = TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    data = _read()
    session = data['sessions'].get(hashlib.sha256(token.encode()).hexdigest())
    if not session or session['expires'] <= time.time():
        raise PermissionError('Acceso denegado: sesion no autenticada o expirada.')
    user = data['users'].get(session['user'])
    if not user or (user['database'], user['digest']) != db_binding():
        raise PermissionError('Acceso denegado: identidad no autorizada para esta base.')
    if permission and permission not in user['permissions']:
        audit('permission', False, session['user'])
        raise PermissionError('Acceso denegado: permiso insuficiente.')
    return {**user, 'subject': session['user']}


def audit(action, allowed, subject=None, sql=None):
    """Sin preguntas, SQL literal, cuentas, nombres ni resultados personales."""
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    item = {'time': time.time(), 'subject': subject, 'action': action, 'allowed': allowed}
    if subject and subject in _read()['users']:
        user = _read()['users'][subject]
        item['role'] = user['role']
        item['scope_sha256'] = hashlib.sha256(json.dumps(user['customers']).encode()).hexdigest()
    if sql:
        item['sql_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    with (ROOT / 'audit.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(item) + '\n')


@contextmanager
def authenticated(token):
    reset = TOKEN.set(token)
    try:
        principal()
        yield
    finally:
        TOKEN.reset(reset)


def subprocess_env():
    # La aplicacion no hereda una identidad de otro turno de Streamlit.
    return {**os.environ, 'PROYECTO_AUTH_TOKEN': TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', ''),
            'PROYECTO_AUTH_MODE': auth_mode(),
            'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}


def authorize_request(consulta):
    """Control preventivo independiente del LLM; el aislamiento SQL sigue siendo obligatorio."""
    import re
    import sqlite3
    import unicodedata
    user = principal()
    text = ''.join(c for c in unicodedata.normalize('NFKD', consulta.casefold()) if not unicodedata.combining(c))
    denied = False
    with closing(sqlite3.connect(Path(user['database']).as_uri() + '?mode=ro', uri=True)) as c:
        for account in re.findall(r'\bcuenta\s+(\d+)\b', text):
            owner = c.execute('SELECT num_cte FROM dim_cuenta WHERE num_cta=?', (int(account),)).fetchone()
            denied |= owner is None or owner[0] not in user['customers']
    for customer in re.findall(r'\bcliente\s+(\d+)\b', text):
        denied |= int(customer) not in user['customers']
    metadata = re.search(r'\b(?:columnas?|metadatos|calidad|faltantes|nulos)\b', text)
    if not (metadata and 'governance' in user['permissions']):
        fields = re.findall(r'\b(?:correo|telefono|rfc|curp|nombre)\b', text)
        denied |= any('pii:' + field not in user['permissions'] for field in fields)
    if denied:
        audit('request_scope', False, user['subject'])
        raise PermissionError('Acceso denegado: solicitud fuera del alcance o campos autorizados.')
    return user


def main():
    p = argparse.ArgumentParser(description='Aprovisionamiento local por operador; no usar con datos reales sin autorizacion.')
    p.add_argument('--ruta', action='store_true', help='Mostrar el almacen efectivo sin credenciales')
    p.add_argument('--migrar-desde', type=Path, help='Importar identidades recuperadas legitimamente')
    p.add_argument('--usuario')
    p.add_argument('--rol', choices=['normal', 'auditor'])
    p.add_argument('--clientes', help='IDs autorizados separados por coma')
    p.add_argument('--permisos', default='', help='Permisos de auditoria separados por coma')
    a = p.parse_args()
    if a.ruta:
        print(ROOT)
        return
    if a.migrar_desde:
        print(f'Identidades nuevas importadas: {migrate_identities(a.migrar_desde)}. No se importan sesiones.')
        return
    if not all((a.usuario, a.rol, a.clientes)):
        p.error('Para crear un usuario indica --usuario, --rol y --clientes; o utiliza --ruta.')
    provision(a.usuario, getpass.getpass('Clave nueva (minimo 16 caracteres): '), a.rol,
              a.clientes.split(','), [x for x in a.permisos.split(',') if x])
    print('Identidad local creada. No se imprime ni guarda la clave en texto plano.')


if __name__ == '__main__':
    main()
