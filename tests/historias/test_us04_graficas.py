"""US-04 · Visualizar resultados analíticos (P1)."""

from __future__ import annotations

import base64
import json

import pytest

from proyecto.charts import generar_grafica, visualizar_resultado_sql

PNG = b"\x89PNG\r\n\x1a\n"


def test_visualizacion_unidad_y_orden_de_importe_nominal():
    sql = "SELECT substr(fec_txn,1,7) mes,SUM(saldo_txn) total FROM vw_txn_firmada GROUP BY 1"
    data = {'columnas': ['mes', 'total'], 'filas': [['2026-03', 3], ['2026-02', 2]]}
    spec = visualizar_resultado_sql(sql, data)['especificacion']
    assert spec['unidad'] == 'MXN'
    assert spec['etiqueta_y'] == 'total (MXN)'
    assert spec['categorias_valores'] == ['2026-02', '2026-03']
    assert spec['valores'] == [2, 3]
    unrelated = visualizar_resultado_sql(sql.replace('vw_txn_firmada', 'otra_fuente'), data)
    assert unrelated['especificacion']['unidad'] == ''


def test_visualizacion_ruta_analitica_no_inspecciona_metadatos():
    from proyecto.orchestration import grafo
    for query in ['¿Cuántos clientes hay por segmento? Muéstralo en una gráfica',
                  '¿Cuál fue el importe total de retiros por mes? Grafícalo',
                  'Compara el promedio de saldos por producto con barras']:
        state = {'consulta': query, 'plan_conversacional': {'rutas': ['data_steward'], 'subpreguntas': []}}
        assert grafo._nodo_planear(state)['plan'] == ['data_analyst']
    assert grafo._rutas_visualizacion('Inspecciona valores nulos por columna', ['data_steward']) == ['data_steward']


@pytest.mark.parametrize('rows,kind', [
    ([['A', 3], [None, 2]], 'bar'),
    ([['2026-01', 12.50], ['2026-02', 20.00]], 'line'),
])
def test_visualizacion_contrato_fiel_y_categorias_sin_clasificar(rows, kind):
    data = {'columnas': ['categoria', 'total'], 'filas': rows, 'truncado': False}
    sql = 'SELECT categoria, SUM(importe) total FROM movimientos GROUP BY categoria'
    chart = visualizar_resultado_sql(sql, data)
    assert chart['png'].startswith(PNG)
    spec = chart['especificacion']
    assert spec['tipo'] == kind and spec['valores'] == [r[1] for r in rows]
    assert spec['categorias_valores'] == [r[0] if r[0] is not None else 'Sin clasificar' for r in rows]
    assert data['filas'] == rows


@pytest.mark.parametrize('sql,data,kind', [
    ('import os; os.system("echo no")', {'columnas': ['x','y'], 'filas': [['a',1]]}, 'bar'),
    ('SELECT x,SUM(y) y FROM datos GROUP BY x', {'columnas': ['x','y'], 'filas': []}, 'bar'),
    ('SELECT x,SUM(y) y FROM datos GROUP BY x', {'columnas': ['x','y'], 'filas': [['a',float('nan')]]}, 'bar'),
    ('SELECT x,SUM(y) y FROM datos GROUP BY x', {'columnas': ['x','y'], 'filas': [['a',1]], 'truncado': True}, 'bar'),
    ('SELECT num_cte x,COUNT(*) y FROM datos GROUP BY num_cte', {'columnas': ['x','y'], 'filas': [[1,1]]}, 'bar'),
    ('SELECT x,SUM(y) y FROM datos GROUP BY x', {'columnas': ['x','y'], 'filas': [['a',1]]}, '__import__("os")'),
])
def test_visualizacion_rechaza_codigo_resultados_incompletos_y_ejes_individuales(sql, data, kind):
    with pytest.raises(ValueError):
        visualizar_resultado_sql(sql, data, kind)


def test_visualizacion_grafo_usa_payload_no_sql_extraido_del_texto(monkeypatch):
    from proyecto.orchestration import grafo
    from unittest.mock import Mock
    payload = {'respuesta': 'Consulta agregada realizada.', 'sql': 'SELECT categoria,COUNT(*) total FROM hechos GROUP BY categoria',
               'datos': {'columnas': ['categoria','total'], 'filas': [['A',3],['B',2]], 'truncado': False}}
    mcp = Mock(return_value=(json.dumps(payload), {'name': 'mcp_analizar_datos', 'estado': 'ok'}, 'ok'))
    monkeypatch.setattr(grafo, '_ejecutar_mcp', mcp)
    state = {'consulta': 'Grafica los totales por categoria', 'perfil': {'rol':'superusuario'}}
    result = grafo._nodo_data_analyst(state)['resultados']['data_analyst']
    assert result['grafica'].startswith(PNG) and result['datos'] == payload['datos']
    assert result['grafica_especificacion']['valores'] == [3,2]
    assert mcp.call_args.args[2]['incluir_datos'] is True
    mcp.assert_called_once()  # No vuelve a ejecutar SQL para dibujar.
    previous = dict(result)
    mcp.reset_mock()
    state.update(consulta='Cámbialo a pastel', analitica_previa=previous,
                 plan_conversacional={'visualizacion': {'tipo':'pie','accion':'reutilizar','consulta_analitica':'Totales por categoria'}})
    reused = grafo._nodo_data_analyst(state)['resultados']['data_analyst']
    assert reused['reutilizado'] and reused['grafica_especificacion']['tipo'] == 'pie'
    assert reused['datos'] == payload['datos'] and reused['consultado_en'] == previous['consultado_en']
    mcp.assert_not_called()
    state['consulta'] = 'Muéstrame solo los últimos seis meses'
    fresh = grafo._nodo_data_analyst(state)['resultados']['data_analyst']
    assert fresh['reutilizado'] is False
    mcp.assert_called_once()


def test_visualizacion_consulta_real_agregada_y_aislamiento(operador, consultar, base_sintetica):
    from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend
    import hashlib
    initial = hashlib.sha256(base_sintetica.read_bytes()).hexdigest()
    sql = 'SELECT segmento,COUNT(DISTINCT num_cte) clientes FROM vw_cliente_publico GROUP BY segmento ORDER BY segmento'
    with operador.sesion('analista'):
        backend = DataAnalystSQLBackend()
        data = backend._consultar(sql)
        assert 'error' not in data
        assert data['filas'] == [list(r) for r in consultar(sql)]
        chart = visualizar_resultado_sql(sql, data)
        assert chart['especificacion']['valores'] == [r[1] for r in data['filas']]
    with operador.sesion('cliente_1'):
        denied = DataAnalystSQLBackend()._consultar(sql)
        assert 'error' in denied
    assert hashlib.sha256(base_sintetica.read_bytes()).hexdigest() == initial


def test_visualizacion_explicacion_mimo_no_devuelve_codigo():
    from proyecto.orchestration.perfil_respuesta import redactar_respuesta, detectar_perfil
    data = {'data_analyst': {'respuesta': 'Resultado verificado: 120.', 'grafica_especificacion': {'tipo': 'line'}}}
    profile = detectar_perfil('auditor', 'Grafica el importe')
    text = redactar_respuesta('Grafica el importe', 'Resultado verificado: 120.', profile, data, 'doble',
        llamar_modelo=lambda *_: 'El importe mostrado es 120.')
    assert text == 'El importe mostrado es 120.'
    invalid = redactar_respuesta('Grafica el importe', 'Resultado verificado: 120.', profile, data, 'doble',
        llamar_modelo=lambda *_: '```python\nimport os\n```')
    assert 'import os' not in invalid and '120' in invalid


def test_visualizacion_no_sustituye_pregunta_por_reformulacion_incompatible(monkeypatch):
    from proyecto.orchestration import grafo
    from unittest.mock import Mock
    original = 'Importe total de retiros por mes, grafícalo'
    payload = {'respuesta': 'Resultado distinto', 'sql': 'SELECT producto,SUM(importe) total FROM movimientos GROUP BY producto',
               'datos': {'columnas': ['producto','total'], 'filas': [['A',100],['B',200]], 'truncado': False}}
    mcp = Mock(return_value=(json.dumps(payload), {'name':'mcp_analizar_datos','estado':'ok'}, 'ok'))
    monkeypatch.setattr(grafo, '_ejecutar_mcp', mcp)
    state = {'consulta': original, 'plan_conversacional': {'visualizacion': {
        'tipo':'pie','accion':'nueva','consulta_analitica':'Transacciones por producto'}}}
    result = grafo._nodo_data_analyst(state)['resultados']['data_analyst']
    assert mcp.call_args.args[2]['consulta'] == original
    assert 'grafica' not in result and 'mensual' in result['grafica_error']


def test_visualizacion_planes_existentes_conservan_grano_y_periodo():
    from proyecto.agents.banking_queries import plan_query
    count = plan_query('Cuantos clientes hay por segmento, graficalo')[1]
    assert 'COUNT(DISTINCT' in count and 'LEFT JOIN' in count and 'Sin clasificar' in count
    year = plan_query('Importe de retiros por mes en 2025')[1]
    assert "desc_txn='RETIRO'" in year and "substr(fec_txn,1,4)='2025'" in year
    assert plan_query('Retiros por mes de los ultimos seis meses') is None


@pytest.fixture
def clientes_por_segmento(consultar):
    filas = consultar(
        "SELECT s.descripcion AS segmento, COUNT(*) AS clientes "
        "FROM dim_cliente c JOIN dim_segmento s USING (cve_segmento) "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    return ["segmento", "clientes"], [list(f) for f in filas]


def test_ca01_la_grafica_usa_las_filas_de_la_consulta(clientes_por_segmento):
    columnas, filas = clientes_por_segmento
    grafica = generar_grafica(columnas, filas)
    assert grafica["png"].startswith(PNG)
    especificacion = grafica["especificacion"]
    assert especificacion["filas"] == len(filas) == 3
    assert especificacion["categorias"] == 3


def test_ca02_titulo_y_ejes(clientes_por_segmento):
    columnas, filas = clientes_por_segmento
    especificacion = generar_grafica(columnas, filas)["especificacion"]
    assert especificacion["eje_x"] == "segmento"
    assert especificacion["eje_y"] == "clientes"
    assert especificacion["titulo"] == "clientes por segmento"

    titulada = generar_grafica(columnas, filas, titulo="Clientes por segmento (número)")
    assert titulada["especificacion"]["titulo"] == "Clientes por segmento (número)"


@pytest.mark.parametrize(
    ("columnas", "filas"),
    [
        (["total_clientes"], [[5000]]),         # un escalar: no hay eje de categorías
        (["segmento", "clientes"], []),          # sin filas
        (["num_cte", "saldo"], [[1, 10.0], [2, 20.0]]),  # identificador como eje
    ],
)
def test_ca03_datos_no_graficables_se_rechazan_con_mensaje(columnas, filas):
    with pytest.raises(ValueError) as error:
        generar_grafica(columnas, filas)
    assert str(error.value)  # mensaje legible para la alerta de la UI


def test_ca03_el_servicio_conserva_la_respuesta_si_falla_la_grafica():
    from proyecto.servicio import _grafica_de_salida

    png, especificacion, tabla = _grafica_de_salida(json.dumps({"error": "No hay filas para graficar."}))
    assert (png, especificacion, tabla) == (None, {}, None)

    salida = {
        "png_base64": base64.b64encode(PNG + b"resto").decode(),
        "columnas": ["segmento", "clientes"],
        "filas": [["Banca masiva", 3750]],
        "especificacion": {"titulo": "clientes por segmento"},
    }
    png, especificacion, tabla = _grafica_de_salida(json.dumps(salida))
    assert png.startswith(PNG)
    assert tabla == [{"segmento": "Banca masiva", "clientes": 3750}]  # misma fuente que la tabla
