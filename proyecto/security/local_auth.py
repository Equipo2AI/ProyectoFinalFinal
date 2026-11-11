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
            ROOT.mkdir(mode=0o700, exist_ok=True)
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

ROOT = Path(__file__).resolve().parents[2] / '.local_auth'
TOKEN = ContextVar('principal_token', default='')
STATE = ROOT / 'identities.json'
DEMO_SUBJECT = 'demo_superuser'
DEMO_PERMISSIONS = ['aggregate', 'audit', 'governance', 'pii:nombre', 'pii:rfc']


def auth_mode():
    """``demo`` evita el login con una identidad sintética; ``local`` lo exige."""
    return (os.environ.get('PROYECTO_AUTH_MODE', 'demo').strip().lower() or 'demo')


def es_modo_demo():
    return auth_mode() == 'demo' and not (
        TOKEN.get() or os.environ.get('PROYECTO_AUTH_TOKEN', '')
    )


def _read():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'users': {}, 'sessions': {}}


def _write(data):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(STATE)


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
    """Identidad sintética para la etapa demo; no crea usuarios ni contraseñas."""
    import sqlite3
    path, digest = db_binding()
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as c:
        customers = sorted(row[0] for row in c.execute('SELECT num_cte FROM dim_cliente'))
    if permission and permission not in DEMO_PERMISSIONS:
        audit('permission', False, DEMO_SUBJECT)
        raise PermissionError('Acceso denegado: permiso insuficiente.')
    return {
        'username': DEMO_SUBJECT,
        'role': 'auditor',
        'customers': customers,
        'permissions': list(DEMO_PERMISSIONS),
        'database': path,
        'digest': digest,
        'subject': DEMO_SUBJECT,
    }


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
    if not token and es_modo_demo():
        return _demo_principal(permission)
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
    ROOT.mkdir(mode=0o700, exist_ok=True)
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
    p.add_argument('--usuario', required=True)
    p.add_argument('--rol', choices=['normal', 'auditor'], required=True)
    p.add_argument('--clientes', required=True, help='IDs autorizados separados por coma')
    p.add_argument('--permisos', default='', help='Permisos de auditoria separados por coma')
    a = p.parse_args()
    provision(a.usuario, getpass.getpass('Clave nueva (minimo 16 caracteres): '), a.rol,
              a.clientes.split(','), [x for x in a.permisos.split(',') if x])
    print('Identidad local creada. No se imprime ni guarda la clave en texto plano.')


if __name__ == '__main__':
    main()
