"""Proyeccion SQLite efimera por identidad; la fuente se abre en mode=ro.

Solo se materializan filas autorizadas en memoria. El SQL del modelo no tiene
acceso al archivo original, a otras bases ni a columnas personales no otorgadas.
No se crea ni regenera ningun archivo de datos.
"""
from pathlib import Path
import sqlite3
from proyecto.security.local_auth import principal

REFERENCE_TABLES = {
    'dim_fecha','dim_tipo_persona','dim_genero','dim_segmento','dim_giro',
    'dim_direccion','dim_subdireccion','dim_territorio','dim_sucursal',
    'dim_promotor','dim_dominio','dim_grupo_producto','dim_producto','dim_txn',
}
ROW_TABLES = {'dim_cliente','dim_cuenta','dim_cliente_persona',
              'fact_saldos_captacoloca_m','fact_txn_captacoloca_d'}


def connect_scoped(path):
    user = principal()
    if str(Path(path).resolve()) != str(Path(user['database']).resolve()):
        raise PermissionError('Acceso denegado a otra base.')
    source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c = sqlite3.connect(':memory:', check_same_thread=False)
    scope = ','.join(str(int(x)) for x in user['customers'])
    allowed = set()
    objects = source.execute("SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'").fetchall()
    try:
        for name, kind, ddl in objects:
            if kind != 'table' or name not in REFERENCE_TABLES | ROW_TABLES:
                continue
            q = '"' + name.replace('"', '""') + '"'
            info = source.execute(f'PRAGMA table_info({q})').fetchall()
            columns = [r[1] for r in info]
            if name == 'dim_cliente' and 'fec_nac' in columns:
                info = [r for r in info if r[1] != 'fec_nac']
                columns = [r[1] for r in info]
                ddl = f'CREATE TABLE {q} (' + ','.join('"'+r[1]+'" '+r[2] + (' PRIMARY KEY' if r[5] else '') for r in info) + ')'
            if name == 'dim_cliente_persona':
                fields = [x for x in ('nombre', 'rfc') if 'pii:' + x in user['permissions'] and 'audit' in user['permissions']]
                if not fields:
                    continue
                info = [r for r in info if r[1] in ['num_cte'] + fields]
                columns = [r[1] for r in info]
                ddl = f'CREATE TABLE {q} (' + ','.join('"'+r[1]+'" '+r[2] for r in info) + ')'
            if 'num_cte' in columns:
                where = f' WHERE num_cte IN ({scope})'
            elif 'num_cta' in columns:
                where = f' WHERE num_cta IN (SELECT num_cta FROM dim_cuenta WHERE num_cte IN ({scope}))'
            elif name in REFERENCE_TABLES:
                where = ''
            else:
                continue
            c.execute(ddl)
            select = ','.join('"'+x+'"' for x in columns)
            rows = source.execute(f'SELECT {select} FROM {q}{where}')
            c.executemany(f'INSERT INTO {q} VALUES (' + ','.join('?' for _ in columns) + ')', rows)
            allowed.add(name)
        for name, kind, ddl in objects:
            if kind == 'view' and name in {'vw_cliente_publico','vw_saldo_mensual','vw_txn_firmada'}:
                c.execute(ddl.replace('c.fec_nac,', ''))
                allowed.add(name)
        c.commit()
    except Exception:
        c.close()
        raise
    finally:
        source.close()
    c.execute('PRAGMA query_only=ON')

    def authorize(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 in allowed or arg1 in {'sqlite_master','sqlite_temp_master'} else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or '').lower() in {'load_extension','readfile','writefile'} else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1.lower() in {'table_info','table_xinfo','foreign_key_list','index_list','index_info','query_only','read_uncommitted'} and not (arg1.lower() in {'query_only','read_uncommitted'} and arg2 is not None):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    c.set_authorizer(authorize)
    return c
