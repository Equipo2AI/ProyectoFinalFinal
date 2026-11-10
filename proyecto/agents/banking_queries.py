"""Planes de lectura para intenciones bancarias acotadas, no respuestas precargadas.

Solo periodos, identificadores numericos y productos reconocidos se interpolan.
El ejecutor SQL existente sigue validando permiso, alcance, campos y lectura.
Las consultas no reconocidas conservan el agente de lenguaje natural.
"""
import re
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def plan_query(question):
    q = normalize(question)
    months = {n: i for i, n in enumerate(('enero','febrero','marzo','abril','mayo','junio',
               'julio','agosto','septiembre','octubre','noviembre','diciembre'), 1)}
    if len(set(re.findall(r'\b20\d{2}\b', q))) > 1:
        return None  # periodos multianuales: conservar el planificador general
    year = re.search(r'\b(20\d{2})\b', q)
    periods = [int(year[1]) * 100 + months[m] for m in re.findall(r'\b('+'|'.join(months)+r')\b', q)] if year else []
    account = re.search(r'\bcuenta\s+(\d+)\b', q)
    customer = re.search(r'\bcliente\s+(\d+)\b', q)
    product = next((code for word, code in [('cheques','CHEQUES'),('ahorro','AHORRO'),
                    ('tarjeta','TDC'),('credito personal','C_PERSONAL')] if word in q), None)
    filters = []
    if account:
        filters.append(f'num_cta={int(account[1])}')
    if customer:
        filters.append(f'num_cte={int(customer[1])}')
    if product:
        filters.append(f"cve_producto='{product}'")
    if periods:
        filters.append('anio_mes IN ('+','.join(str(p) for p in periods)+')')
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    if re.search(r'\b(nombre|rfc)\b', q) and customer:
        fields = [x for x in ('nombre','rfc') if re.search(r'\b'+x+r'\b', q)]
        return 'Identidad autorizada para auditoria', 'SELECT num_cte,'+','.join(fields)+f' FROM dim_cliente_persona WHERE num_cte={int(customer[1])}'
    if 'perfil' in q and customer:
        return 'Perfil autorizado', f'SELECT num_cte,segmento,sucursal,promotor FROM vw_cliente_publico WHERE num_cte={int(customer[1])}'
    if 'sucursales' in q and 'promotores' in q:
        return 'Catalogos bancarios', 'SELECT (SELECT COUNT(*) FROM dim_sucursal) sucursales,(SELECT COUNT(*) FROM dim_promotor) promotores'
    if 'clientes' in q and 'segmento' in q and not re.search(r'saldo|promedio', q):
        return 'Clientes por segmento dentro del alcance autorizado', 'SELECT segmento,COUNT(*) clientes FROM vw_cliente_publico GROUP BY segmento ORDER BY segmento'
    if 'retiros' in q and re.search(r'por mes|mensual', q):
        return 'Importe de retiros por mes', "SELECT substr(fec_txn,1,7) mes,ROUND(SUM(saldo_txn),2) importe_retiros FROM vw_txn_firmada WHERE desc_txn='RETIRO' GROUP BY substr(fec_txn,1,7) ORDER BY mes"
    if 'mayor' in q and 'saldo' in q:
        top = re.search(r'\b(\d+)\s+clientes', q)
        if not top or not periods:
            return None
        limit = min(int(top[1]), 50)
        base = f"anio_mes={periods[-1]}"
        if 'cheques' in q and 'ahorro' in q:
            base += " AND cve_producto IN ('CHEQUES','AHORRO')"
        return 'Clientes con mayor saldo autorizado', f'SELECT num_cte,ROUND(SUM(saldo_mes),2) saldo_combinado FROM vw_saldo_mensual WHERE {base} GROUP BY num_cte ORDER BY SUM(saldo_mes) DESC,num_cte LIMIT {limit}'
    if 'saldo' in q and 'promedio' in q and 'segmento' in q and periods:
        return 'Saldo promedio por segmento', "SELECT c.segmento,ROUND(AVG(s.saldo_mes),2) saldo_promedio FROM vw_saldo_mensual s JOIN vw_cliente_publico c USING(num_cte) WHERE s.anio_mes="+str(periods[-1])+(f" AND s.cve_producto='{product}'" if product else '')+' GROUP BY c.segmento ORDER BY c.segmento'
    if 'saldo' in q and 'por producto' in q and periods:
        return 'Saldo total por producto', f'SELECT cve_producto,ROUND(SUM(saldo_mes),2) saldo_total FROM vw_saldo_mensual WHERE anio_mes={periods[-1]} GROUP BY cve_producto ORDER BY cve_producto'
    if re.search(r'creci|crecimiento|variacion', q) and 'saldo' in q and len(periods) == 2:
        scope = [x for x in filters if not x.startswith('anio_mes')]
        condition = ' AND '.join(scope + ['anio_mes IN ('+','.join(map(str, periods))+')'])
        return 'Crecimiento entre cierres disponibles', f'SELECT num_cta,MIN(anio_mes) mes_inicial,MAX(anio_mes) mes_final,MAX(CASE WHEN anio_mes={periods[0]} THEN saldo_mes END) saldo_inicial,MAX(CASE WHEN anio_mes={periods[1]} THEN saldo_mes END) saldo_final,ROUND(MAX(CASE WHEN anio_mes={periods[1]} THEN saldo_mes END)-MAX(CASE WHEN anio_mes={periods[0]} THEN saldo_mes END),2) crecimiento FROM vw_saldo_mensual WHERE {condition} GROUP BY num_cta'
    if re.search(r'gaste|gastos|compras', q) and product and periods:
        conditions = [x for x in filters if not x.startswith('anio_mes')]
        conditions += [f'cve_fecha_informacion/100={periods[-1]}', "desc_txn='COMPRAS'"]
        return 'Gasto en compras (no incluye pagos ni disposiciones)', 'SELECT num_cta,ROUND(SUM(saldo_txn),2) gasto_compras FROM vw_txn_firmada WHERE '+' AND '.join(conditions)+' GROUP BY num_cta'
    if re.search(r'movimientos|estado de cuenta', q) and periods:
        conditions = [x for x in filters if not x.startswith('anio_mes')]
        conditions.append(f'cve_fecha_informacion/100={periods[-1]}')
        return 'Movimientos de la cuenta en el periodo solicitado', 'SELECT num_cta,fec_txn,desc_txn,saldo_txn,importe_firmado FROM vw_txn_firmada WHERE '+' AND '.join(conditions)+' ORDER BY num_cta,fec_txn,cve_txn'
    if 'saldo' in q and periods:
        return 'Saldos de cierre autorizados', 'SELECT num_cte,num_cta,cve_producto,fec_corte,saldo_apertura,saldo_mes FROM vw_saldo_mensual'+where+' ORDER BY num_cta,anio_mes'
    if re.search(r'cuentas.*(?:tengo|contratad)|mis cuentas', q):
        return 'Tus cuentas contratadas', 'SELECT c.num_cta,p.descripcion producto FROM dim_cuenta c JOIN dim_producto p USING(cve_producto) ORDER BY c.num_cta'
    return None


def format_result(title, result, sql):
    if 'error' in result:
        return 'No se pudo completar la consulta autorizada: '+str(result['error'])
    columns, rows = result['columnas'], result['filas']
    def cell(value):
        if value is None:
            return 'Sin dato disponible'
        return str(value).replace('|', '\\|').replace('\n', ' ')
    labels = {'num_cta':'Cuenta','num_cte':'Cliente','saldo_mes':'Saldo de cierre','fec_corte':'Fecha de corte','fec_txn':'Fecha','desc_txn':'Movimiento','saldo_txn':'Importe','importe_firmado':'Importe con signo'}
    text = '**'+title+'**\n\n'
    if not rows:
        text += 'No hay registros autorizados para el periodo o los filtros solicitados.'
    else:
        text += '| '+' | '.join(labels.get(c,c.replace('_',' ').capitalize()) for c in columns)+' |\n| '+' | '.join('---' for _ in columns)+' |\n'
        text += '\n'.join('| '+' | '.join(cell(v) for v in row)+' |' for row in rows)
    if result.get('truncado'):
        text += '\n\nLa herramienta alcanzo el limite de filas; el resultado no es completo.'
    text += '\n\nFuente: SQLite, dentro del alcance de la identidad autenticada. Importes monetarios en MXN.'
    text += '\n\nSQL ejecutado (solo lectura):\n```sql\n'+sql+'\n```'
    return text
