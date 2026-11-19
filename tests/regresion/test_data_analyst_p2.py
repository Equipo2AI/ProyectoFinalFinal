from __future__ import annotations

import json
import time

from sqlalchemy import create_engine

import proyecto.mcp_servers.data_analyst_backend as backend_module
from proyecto.mcp_servers.data_analyst_backend import DataAnalystSQLBackend
from proyecto.mcp_servers.metadata_cache import MetadataCache


class BackendPrueba(DataAnalystSQLBackend):
    """Backend con un engine de prueba y sin el wrapper de alcance local."""

    def __init__(self, url_db: str, engine):
        super().__init__(url_db)
        self._engine_prueba = engine

    def _get_engine(self):
        return self._engine_prueba


def _tool(backend: DataAnalystSQLBackend, nombre: str):
    return next(t for t in backend._tools if t.name == nombre)


def _principal(db_path, *, pii: bool = False, permisos=None):
    if permisos is None:
        permisos = ["audit", "pii:nombre"] if pii else []
    return {
        "subject": "prueba",
        "customers": [],
        "permissions": permisos,
        "role": "auditor",
        "database": str(db_path),
    }


def _preparar_base(tmp_path):
    db_path = tmp_path / "analitica.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conexion:
        conexion.exec_driver_sql(
            "CREATE TABLE demo (id INTEGER PRIMARY KEY, nombre TEXT)"
        )
        conexion.exec_driver_sql(
            "INSERT INTO demo (nombre) VALUES ('uno'), ('dos')"
        )
        conexion.exec_driver_sql(
            "CREATE TABLE dim_cliente_persona (id INTEGER PRIMARY KEY, nombre TEXT)"
        )
        conexion.exec_driver_sql(
            "CREATE VIEW vw_demo AS SELECT id, nombre FROM demo"
        )
    return db_path, engine


def _backend(db_path, engine, cache_path):
    backend = BackendPrueba(f"sqlite:///{db_path}", engine)
    backend._metadata_cache = MetadataCache(cache_path, ttl=3600)
    return backend


def test_resumen_metadatos_incluye_esquema_y_metricas(
    tmp_path, monkeypatch
):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    resumen = _backend(db_path, engine, cache_path)._resumen_metadatos()

    assert "Catálogo de metadatos ya cargado" in resumen
    assert "TABLA `demo`" in resumen
    assert "VISTA `vw_demo`" in resumen
    assert "nombre" in resumen
    assert "Métricas certificadas:" in resumen
    assert "clientes" in resumen
    assert "dim_cliente_persona" not in resumen


def test_resumen_metadatos_incluye_pii_solo_con_permiso(
    tmp_path, monkeypatch
):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module,
        "principal",
        lambda *a, **k: _principal(db_path, pii=True),
    )

    resumen = _backend(db_path, engine, cache_path)._resumen_metadatos()

    assert "dim_cliente_persona" in resumen


def test_prompt_agente_incluye_catalogo_y_regla_de_no_repetir(
    tmp_path, monkeypatch
):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    prompt = _backend(db_path, engine, cache_path)._prompt_agente()

    assert "Catálogo de metadatos ya cargado" in prompt
    assert "TABLA `demo`" in prompt
    assert "No llames listar_tablas ni catalogo_metricas" in prompt


def test_prompt_agente_puede_desactivar_catalogo(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )
    monkeypatch.setenv("DATA_ANALYST_PROMPT_CATALOG", "0")

    prompt = _backend(db_path, engine, cache_path)._prompt_agente()

    assert "Catálogo de metadatos ya cargado" not in prompt


def test_resumen_metadatos_reutiliza_cache(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )
    llamadas = []
    original = backend_module.inspect

    def inspect_contado(engine_):
        llamadas.append(engine_)
        return original(engine_)

    monkeypatch.setattr(backend_module, "inspect", inspect_contado)
    backend = _backend(db_path, engine, cache_path)

    backend._resumen_metadatos()
    primeras = len(llamadas)
    assert primeras > 0

    backend._resumen_metadatos()
    assert len(llamadas) == primeras


def test_cache_persiste_entre_instancias_y_expira(tmp_path):
    ruta = tmp_path / "cache.json"
    cache = MetadataCache(ruta, ttl=3600)
    cache.set("clave", {"tablas": ["demo"]})

    otra = MetadataCache(ruta, ttl=3600)
    assert otra.get("clave") == {"tablas": ["demo"]}

    expira = MetadataCache(ruta, ttl=0.01)
    expira.set("otra", {"valor": 1})
    time.sleep(0.02)
    assert expira.get("otra") is None


def test_cache_deshabilitado_no_escribe(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ANALYST_METADATA_CACHE", "0")
    cache = MetadataCache()
    assert not cache.habilitado
    cache.set("clave", {"valor": 1})
    assert cache.get("clave") is None


def test_cache_escritura_fallida_no_rompe_la_consulta(tmp_path):
    archivo = tmp_path / "no-es-directorio"
    archivo.write_text("x", encoding="utf-8")
    cache = MetadataCache(archivo / "cache.json", ttl=3600)

    cache.set("clave", {"valor": 1})

    # La instancia conserva el valor en memoria, pero no se creó el archivo.
    assert cache.get("clave") == {"valor": 1}
    assert MetadataCache(archivo / "cache.json", ttl=3600).get("clave") is None


def test_listar_tablas_reutiliza_esquema_entre_instancias(
    tmp_path, monkeypatch
):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    llamadas = []
    original = backend_module.inspect

    def inspect_contado(engine_):
        llamadas.append(engine_)
        return original(engine_)

    monkeypatch.setattr(backend_module, "inspect", inspect_contado)

    primero = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )
    segundo = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )

    assert primero == segundo
    assert "demo" in primero["tablas"]
    assert "vw_demo" in primero["vistas"]
    assert len(llamadas) == 1


def test_cache_se_invalida_cuando_cambia_el_esquema(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    primero = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )
    assert "nueva" not in primero["tablas"]

    with engine.begin() as conexion:
        conexion.exec_driver_sql("CREATE TABLE nueva (id INTEGER PRIMARY KEY)")

    segundo = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )
    assert "nueva" in segundo["tablas"]


def test_describir_tabla_cachea_esquema_pero_no_el_conteo(
    tmp_path, monkeypatch
):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    llamadas = []
    original = backend_module.inspect

    def inspect_contado(engine_):
        llamadas.append(engine_)
        return original(engine_)

    monkeypatch.setattr(backend_module, "inspect", inspect_contado)

    primero = json.loads(
        _tool(
            _backend(db_path, engine, cache_path), "describir_tabla"
        ).invoke({"tabla": "demo"})
    )
    assert primero["filas"] == 2
    assert [c["nombre"] for c in primero["columnas"]] == ["id", "nombre"]

    with engine.begin() as conexion:
        conexion.exec_driver_sql("INSERT INTO demo (nombre) VALUES ('tres')")

    segundo = json.loads(
        _tool(
            _backend(db_path, engine, cache_path), "describir_tabla"
        ).invoke({"tabla": "demo"})
    )

    assert segundo["filas"] == 3
    assert len(llamadas) == 1


def test_cache_distingue_campos_pii(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    estado = {"permisos": ["audit", "pii:nombre"]}
    monkeypatch.setattr(
        backend_module,
        "principal",
        lambda *a, **k: _principal(db_path, permisos=estado["permisos"]),
    )

    backend = _backend(db_path, engine, cache_path)
    clave_nombre = backend._metadata_key(
        "describir_tabla",
        tabla="dim_cliente_persona",
        permisos=backend._cache_permissions_key(),
    )

    estado["permisos"] = ["audit", "pii:rfc"]
    clave_rfc = backend._metadata_key(
        "describir_tabla",
        tabla="dim_cliente_persona",
        permisos=backend._cache_permissions_key(),
    )

    assert clave_nombre != clave_rfc


def test_cache_separa_permisos_de_pii(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    estado = {"pii": False}
    monkeypatch.setattr(
        backend_module,
        "principal",
        lambda *a, **k: _principal(db_path, pii=estado["pii"]),
    )

    sin_pii = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )
    assert "dim_cliente_persona" not in sin_pii["tablas"]

    estado["pii"] = True
    con_pii = json.loads(
        _tool(_backend(db_path, engine, cache_path), "listar_tablas").invoke({})
    )
    assert "dim_cliente_persona" in con_pii["tablas"]


def test_catalogo_se_cachea_entre_instancias(tmp_path, monkeypatch):
    db_path, engine = _preparar_base(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(
        backend_module, "principal", lambda *a, **k: _principal(db_path)
    )

    llamadas = []
    original = backend_module.listar_catalogo_metricas

    def catalogo_contado():
        llamadas.append(1)
        return original()

    monkeypatch.setattr(
        backend_module, "listar_catalogo_metricas", catalogo_contado
    )

    primero = json.loads(
        _tool(
            _backend(db_path, engine, cache_path), "catalogo_metricas"
        ).invoke({})
    )
    segundo = json.loads(
        _tool(
            _backend(db_path, engine, cache_path), "catalogo_metricas"
        ).invoke({})
    )

    assert primero == segundo
    assert len(llamadas) == 1
