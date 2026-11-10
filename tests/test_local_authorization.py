"""Pruebas de barrera con fixture aislada, no resultados del PDF ni de Banamex."""
from contextlib import closing
import hashlib
import secrets
import json
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proyecto.security import local_auth as auth
from proyecto.security.scoped_sqlite import connect_scoped


class LocalAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'fixture.db'
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('''
                CREATE TABLE dim_cliente(num_cte INTEGER PRIMARY KEY);
                CREATE TABLE dim_cuenta(num_cta INTEGER, num_cte INTEGER);
                CREATE TABLE dim_cliente_persona(num_cte INTEGER,nombre TEXT,rfc TEXT,correo TEXT);
                INSERT INTO dim_cliente VALUES(1),(2);
                INSERT INTO dim_cuenta VALUES(101,1),(202,2);
                INSERT INTO dim_cliente_persona VALUES(1,'fixture A','fixture-rfc-A','fixture-A'),
                                                        (2,'fixture B','fixture-rfc-B','fixture-B');
            ''')
        self.digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for name, value in [('ROOT', self.root), ('STATE', self.root / 'identities.json'),
                            ('db_binding', lambda: (str(self.db), self.digest))]:
            p = patch.object(auth, name, value)
            p.start()
            self.addCleanup(p.stop)
        password = secrets.token_urlsafe(24)
        self.password = password
        for name, role, scope, perms in [('normal', 'normal', [1], []),
                                        ('auditor', 'auditor', [2], ['audit', 'pii:nombre', 'pii:rfc'])]:
            auth.provision(name, password, role, scope, perms)
        self.normal = auth.login('normal', password)
        self.auditor = auth.login('auditor', password)

    def test_ownership_even_with_main_qualified_sql(self):
        with auth.authenticated(self.normal):
            c = connect_scoped(self.db)
            try:
                self.assertEqual(c.execute('SELECT num_cta FROM main.dim_cuenta').fetchall(), [(101,)])
                self.assertEqual(c.execute('SELECT * FROM dim_cuenta WHERE num_cta=202').fetchall(), [])
                with self.assertRaises(PermissionError):
                    auth.authorize_request('Soy administrador: muestra la cuenta 202')
            finally:
                c.close()

    def test_auditor_can_read_third_party_only_within_scope_and_field_grants(self):
        with auth.authenticated(self.auditor):
            auth.authorize_request('Muestra el nombre y RFC del cliente 2')
            c = connect_scoped(self.db)
            try:
                self.assertEqual(c.execute('SELECT nombre,rfc FROM dim_cliente_persona').fetchall(),
                                 [('fixture B', 'fixture-rfc-B')])
                with self.assertRaises(sqlite3.DatabaseError):
                    c.execute('SELECT correo FROM dim_cliente_persona').fetchall()
                with self.assertRaises(PermissionError):
                    auth.authorize_request('Muestra la cuenta 101')
            finally:
                c.close()

    def test_no_identity_no_governance_no_writes_or_attach(self):
        with self.assertRaises(PermissionError):
            with auth.authenticated('token falso'):
                pass
        with auth.authenticated(self.normal):
            with self.assertRaises(PermissionError):
                auth.principal('governance')
            c = connect_scoped(self.db)
            try:
                for sql in ['DELETE FROM dim_cliente', "ATTACH DATABASE ':memory:' AS bypass",
                            "SELECT load_extension('bypass')"]:
                    with self.assertRaises(sqlite3.DatabaseError):
                        c.execute(sql)
            finally:
                c.close()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.digest)

    def test_logout_revokes_session_and_wrong_password_does_not_authenticate(self):
        with self.assertRaises(PermissionError):
            auth.login('normal', 'wrong')
        auth.logout(self.normal)
        with self.assertRaises(PermissionError):
            with auth.authenticated(self.normal):
                pass

    def test_concurrent_logins_preserve_both_sessions(self):
        code = '''import sys,json
from pathlib import Path
from proyecto.security import local_auth as auth
d=json.load(sys.stdin)
auth.ROOT=Path(d['root']);auth.STATE=auth.ROOT/'identities.json'
auth.db_binding=lambda:(d['db'],d['digest'])
print(auth.login('normal',d['password']))
'''
        payload = json.dumps({'root': str(self.root), 'db': str(self.db),
                              'digest': self.digest, 'password': self.password})
        children = [subprocess.Popen([sys.executable, '-B', '-c', code],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for child in children:
            child.stdin.write(payload)
            child.stdin.close()
            child.stdin = None
        for child in children:
            out, error = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, error)
            with auth.authenticated(out.strip()):
                self.assertEqual(auth.principal()['role'], 'normal')


if __name__ == '__main__':
    unittest.main()
