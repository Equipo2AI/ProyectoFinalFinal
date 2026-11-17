"""Genera un reporte HTML local con los resultados de DeepEval.

    uv run python deepeval/reporte.py            # lee deepeval/resultados
    uv run python deepeval/reporte.py --abrir    # y lo abre en el navegador

Lee los ``test_run_*.json`` que DeepEval guarda cuando ``DEEPEVAL_RESULTS_FOLDER``
está definido y escribe un único archivo ``reporte.html`` autocontenido: no usa
internet ni librerías externas, así que los datos no salen de la máquina.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
MAX_TEXTO = 20_000  # tope por campo de texto para que el HTML no crezca sin límite
_RE_FECHA = re.compile(r"(\d{8})_(\d{6})")


# --------------------------------------------------------------- lectura


def _campo(datos: dict, *nombres, defecto=None):
    """Lee un campo aceptando camelCase o snake_case (cambia entre versiones)."""
    for nombre in nombres:
        if isinstance(datos, dict) and datos.get(nombre) is not None:
            return datos[nombre]
    return defecto


def _texto(valor) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        try:
            valor = json.dumps(valor, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            valor = str(valor)
    if len(valor) > MAX_TEXTO:
        valor = valor[:MAX_TEXTO] + f"\n[… texto truncado a {MAX_TEXTO:,} caracteres …]"
    return valor


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None  # descarta NaN


def _nombres_herramientas(lista) -> list[str]:
    nombres = []
    for item in lista or []:
        nombre = item.get("name") if isinstance(item, dict) else item
        if nombre:
            nombres.append(str(nombre))
    return nombres


def _metrica(cruda: dict) -> dict:
    return {
        "nombre": str(_campo(cruda, "name", "metric", defecto="métrica")),
        "puntaje": _numero(_campo(cruda, "score")),
        "umbral": _numero(_campo(cruda, "threshold")),
        "exito": bool(_campo(cruda, "success", defecto=False)),
        "razon": _texto(_campo(cruda, "reason")),
        "error": _texto(_campo(cruda, "error")),
        "modelo": _texto(_campo(cruda, "evaluationModel", "evaluation_model")),
    }


def _caso(crudo: dict, posicion: int) -> dict:
    metricas = [
        _metrica(m)
        for m in _campo(crudo, "metricsData", "metrics_data", defecto=[])
        if isinstance(m, dict)
    ]
    exito = _campo(crudo, "success")
    if exito is None:  # sin veredicto explícito: pasa si pasan todas sus métricas
        exito = bool(metricas) and all(m["exito"] for m in metricas)
    contexto = _campo(crudo, "retrievalContext", "retrieval_context", defecto=[])
    if isinstance(contexto, str):
        contexto = [contexto]
    return {
        "id": str(_campo(crudo, "name", defecto=f"caso-{posicion + 1}")),
        "pregunta": _texto(_campo(crudo, "input")),
        "respuesta": _texto(_campo(crudo, "actualOutput", "actual_output")),
        "esperada": _texto(_campo(crudo, "expectedOutput", "expected_output")),
        "contexto": [_texto(c) for c in contexto or []][:20],
        "herramientas": _nombres_herramientas(_campo(crudo, "toolsCalled", "tools_called")),
        "herramientas_esperadas": _nombres_herramientas(
            _campo(crudo, "expectedTools", "expected_tools")
        ),
        "etiquetas": [str(e) for e in _campo(crudo, "tags", defecto=[]) or []],
        "exito": bool(exito),
        "duracion": _numero(_campo(crudo, "runDuration", "run_duration", "completionTime")),
        "metricas": metricas,
    }


def _fecha(ruta: Path) -> datetime:
    coincidencia = _RE_FECHA.search(ruta.name)
    if coincidencia:
        try:
            return datetime.strptime("".join(coincidencia.groups()), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(ruta.stat().st_mtime)


def leer_corrida(ruta: Path) -> dict | None:
    """Convierte un JSON de DeepEval en una corrida normalizada; None si no sirve."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"  [omitido] {ruta.name}: no se pudo leer ({error})", file=sys.stderr)
        return None
    if isinstance(datos, dict):
        datos = _campo(datos, "testRunData", "test_run_data", defecto=datos)
    if not isinstance(datos, dict):
        print(f"  [omitido] {ruta.name}: formato no reconocido", file=sys.stderr)
        return None
    crudos = _campo(datos, "testCases", "test_cases", defecto=[])
    casos = [_caso(c, i) for i, c in enumerate(crudos) if isinstance(c, dict)]
    if not casos:
        print(f"  [omitido] {ruta.name}: no contiene casos de prueba", file=sys.stderr)
        return None
    # Un id repetido (p. ej. --repeat) rompería la comparación entre corridas.
    vistos: dict[str, int] = {}
    for caso in casos:
        vistos[caso["id"]] = vistos.get(caso["id"], 0) + 1
        if vistos[caso["id"]] > 1:
            caso["id"] = f"{caso['id']} #{vistos[caso['id']]}"
    fecha = _fecha(ruta)
    return {
        "archivo": ruta.name,
        "fecha": fecha.isoformat(timespec="seconds"),
        "duracion": _numero(_campo(datos, "runDuration", "run_duration")),
        "casos": casos,
    }


def buscar_archivos(carpeta: Path) -> list[Path]:
    if carpeta.is_file():
        return [carpeta]
    archivos = sorted(carpeta.glob("test_run_*.json"), key=_fecha)
    if not archivos and carpeta.is_dir():
        archivos = sorted(
            (p for p in carpeta.glob("*.json") if not p.name.startswith(".")), key=_fecha
        )
    return archivos


def metadatos_yaml() -> dict:
    """Datos de los YAML que el JSON no trae: archivo de origen y fallo conocido."""
    try:
        sys.path.insert(0, str(AQUI))
        from cargador import cargar_casos

        return {
            caso.id: {
                "origen": caso.archivo.rsplit(".", 1)[0],
                "xfail": caso.xfail or "",
                "rol": str((caso.perfil or {}).get("rol") or ""),
                "criterio": caso.criterio or "",
            }
            for caso in cargar_casos()
        }
    except Exception as error:  # noqa: BLE001 - el reporte funciona sin los YAML
        print(f"  [aviso] no se leyeron los YAML de casos: {error}", file=sys.stderr)
        return {}


def carpeta_por_defecto() -> Path:
    candidatas = [
        os.getenv("DEEPEVAL_RESULTS_FOLDER"),
        AQUI / "resultados",
        RAIZ / ".deepeval" / ".latest_test_run.json",
    ]
    for candidata in candidatas:
        if candidata and Path(candidata).exists():
            return Path(candidata)
    return AQUI / "resultados"


# ------------------------------------------------------------------ HTML

PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evaluación del agente · DeepEval</title>
<style>
:root{
  color-scheme:light;
  --plano:#f9f9f7;--sup:#fcfcfb;--tinta:#0b0b0b;--tinta2:#52514e;--tenue:#898781;
  --rejilla:#e1e0d9;--eje:#c3c2b7;--borde:rgba(11,11,11,.10);--realce:rgba(11,11,11,.04);
  --serie:#2a78d6;--serie-suave:#cde2fb;
  --bien:#0ca30c;--bien-texto:#006300;--mal:#d03b3b;--aviso:#fab219;--serio:#ec835a;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  color-scheme:dark;
  --plano:#0d0d0d;--sup:#1a1a19;--tinta:#f0efec;--tinta2:#c3c2b7;--tenue:#898781;
  --rejilla:#2c2c2a;--eje:#383835;--borde:rgba(255,255,255,.10);--realce:rgba(255,255,255,.05);
  --serie:#3987e5;--serie-suave:#184f95;--bien-texto:#0ca30c;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--plano);color:var(--tinta);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1240px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:20px;margin:0}
h2{font-size:14px;margin:0 0 2px;font-weight:600}
.sub{color:var(--tinta2);font-size:12.5px;margin:0 0 12px}
header{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:flex-end;justify-content:space-between;margin-bottom:20px}
.controles{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end}
label.campo{display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:var(--tinta2)}
select,input[type=search]{font:inherit;color:var(--tinta);background:var(--sup);border:1px solid var(--eje);border-radius:6px;padding:6px 8px;min-width:0}
input[type=search]{width:240px}
.tarjeta{background:var(--sup);border:1px solid var(--borde);border-radius:10px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.kpi .rotulo{font-size:12px;color:var(--tinta2)}
.kpi .valor{font-size:24px;font-weight:650;line-height:1.2;font-variant-numeric:tabular-nums}
.kpi .nota{font-size:12px;color:var(--tinta2);min-height:18px}
.sube{color:var(--bien-texto)}.baja{color:var(--mal)}
.rejilla{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px;margin-bottom:16px}
.cambios{margin-bottom:16px}
.cambios .fila{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
.cambios .fila>b{font-size:12.5px;min-width:150px}
.ficha{font:12px var(--mono);border:1px solid var(--eje);border-radius:999px;padding:2px 9px;background:none;color:var(--tinta);cursor:pointer}
.ficha:hover{background:var(--realce)}
svg{display:block;width:100%;height:auto;overflow:visible}
svg text{fill:var(--tinta2);font-size:11.5px}
svg .valor{fill:var(--tinta);font-variant-numeric:tabular-nums}
.barra{cursor:default}.barra:hover rect.dato,.barra:focus rect.dato{opacity:.8}
.filtros{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end;margin-bottom:12px}
.segmentos{display:inline-flex;border:1px solid var(--eje);border-radius:6px;overflow:hidden}
.segmentos button{font:inherit;font-size:12.5px;border:0;background:var(--sup);color:var(--tinta2);padding:6px 10px;cursor:pointer;border-right:1px solid var(--eje)}
.segmentos button:last-child{border-right:0}
.segmentos button[aria-pressed=true]{background:var(--tinta);color:var(--sup)}
table{width:100%;border-collapse:collapse}
th{font-size:11.5px;font-weight:600;color:var(--tinta2);text-align:left;padding:6px 10px;border-bottom:1px solid var(--eje);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--rejilla);vertical-align:top}
tr.caso{cursor:pointer}tr.caso:hover{background:var(--realce)}
tr.caso:focus-visible{outline:2px solid var(--serie);outline-offset:-2px}
.id{font:12.5px var(--mono);font-weight:600}
.pregunta{color:var(--tinta2);font-size:12.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.estado{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;white-space:nowrap}
.punto{width:16px;height:16px;border-radius:50%;display:inline-grid;place-items:center;color:#fff;font-size:10px;font-weight:700;flex:none}
.punto.paso{background:var(--bien)}.punto.fallo{background:var(--mal)}
.punto.conocido{background:var(--serio)}.punto.xpass{background:var(--aviso);color:#0b0b0b}
.etq{display:inline-block;font-size:11px;color:var(--tinta2);border:1px solid var(--rejilla);border-radius:4px;padding:0 5px;margin:0 3px 3px 0}
.met{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;border:1px solid var(--rejilla);border-radius:5px;padding:1px 6px;margin:0 4px 4px 0;white-space:nowrap}
.met i{font-style:normal;font-weight:700}.met.ok i{color:var(--bien-texto)}.met.no i,.met.err i{color:var(--mal)}
.met b{font-variant-numeric:tabular-nums}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--tinta2)}
tr.detalle td{background:var(--realce);padding:14px 16px 18px}
.dgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px 22px}
.dgrid h3{font-size:11.5px;color:var(--tinta2);margin:0 0 4px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12.5px/1.5 var(--mono);background:var(--sup);border:1px solid var(--borde);border-radius:6px;padding:9px 11px;max-height:340px;overflow:auto}
.mdet{background:var(--sup);border:1px solid var(--borde);border-radius:6px;padding:9px 11px;margin-bottom:8px}
.mdet .cab{display:flex;gap:8px;align-items:center;justify-content:space-between;font-weight:600;font-size:12.5px}
.escala{position:relative;height:6px;border-radius:3px;background:var(--rejilla);margin:7px 0 6px}
.escala span{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--serie)}
.escala em{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--tinta)}
.mdet p{margin:0;font-size:12.5px;color:var(--tinta2)}
.mdet .err{color:var(--mal)}
.vacio{color:var(--tenue);font-size:13px;padding:22px;text-align:center}
#tip{position:fixed;pointer-events:none;z-index:9;background:var(--tinta);color:var(--sup);font-size:12px;line-height:1.4;padding:6px 9px;border-radius:6px;max-width:280px;opacity:0;transition:opacity .08s}
footer{color:var(--tenue);font-size:12px;margin-top:18px}
@media print{tr.caso{cursor:auto}.filtros,.controles{display:none}}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Evaluación del agente</h1>
      <p class="sub" id="resumen" style="margin:2px 0 0"></p>
    </div>
    <div class="controles">
      <label class="campo">Corrida<select id="selCorrida"></select></label>
      <label class="campo">Comparar con<select id="selBase"></select></label>
    </div>
  </header>

  <section class="kpis" id="kpis"></section>
  <section class="tarjeta cambios" id="cambios" hidden></section>

  <section class="rejilla">
    <div class="tarjeta"><h2>Aprobación por etiqueta</h2><p class="sub">Porcentaje de casos aprobados en cada grupo de la corrida</p><div id="gEtiquetas"></div></div>
    <div class="tarjeta"><h2>Puntaje promedio por métrica</h2><p class="sub">Escala de 0 a 1; a la derecha, casos que superan el umbral</p><div id="gMetricas"></div></div>
    <div class="tarjeta" id="tHistorico"><h2>Aprobación por corrida</h2><p class="sub">Cada punto es una corrida; haz clic para abrirla</p><div id="gHistorico"></div></div>
  </section>

  <section class="tarjeta">
    <div class="filtros">
      <div class="segmentos" id="fEstado" role="group" aria-label="Filtrar por estado"></div>
      <label class="campo">Etiqueta<select id="fEtiqueta"></select></label>
      <label class="campo">Ordenar por<select id="fOrden">
        <option value="estado">Fallidos primero</option><option value="id">Identificador</option>
        <option value="puntaje">Puntaje más bajo</option><option value="tiempo">Más lentos</option></select></label>
      <label class="campo">Buscar<input type="search" id="fBuscar" placeholder="id, pregunta o respuesta"></label>
    </div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Estado</th><th>Caso</th><th>Etiquetas</th><th>Métricas</th><th>Cambio</th><th class="num">Tiempo</th></tr></thead>
        <tbody id="cuerpo"></tbody>
      </table>
    </div>
    <div class="vacio" id="vacio" hidden>Ningún caso coincide con los filtros.</div>
  </section>
  <footer id="pie"></footer>
</main>
<div id="tip" role="tooltip"></div>
<script id="datos" type="application/json">__DATOS__</script>
<script>
"use strict";
const DATOS = JSON.parse(document.getElementById("datos").textContent);
const CORRIDAS = DATOS.corridas;            // de la más antigua a la más reciente
const META = DATOS.meta || {};
const $ = (id) => document.getElementById(id);
const estado = { corrida: CORRIDAS.length - 1, base: CORRIDAS.length - 2, filtro: "todos", etiqueta: "", orden: "estado", buscar: "", abiertos: new Set() };

const ESTADOS = {
  paso:     { rotulo: "Aprobado",       icono: "✓" },
  fallo:    { rotulo: "Fallido",        icono: "✕" },
  conocido: { rotulo: "Fallo conocido", icono: "!" },
  xpass:    { rotulo: "Ya pasa",        icono: "★" },
};

// ---------- utilidades
function el(etiqueta, attrs, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") nodo.className = v;
    else if (k === "text") nodo.textContent = v;
    else if (k.startsWith("on")) nodo.addEventListener(k.slice(2), v);
    else nodo.setAttribute(k, v === true ? "" : v);
  }
  for (const h of hijos.flat()) if (h !== null && h !== undefined && h !== false) nodo.append(h);
  return nodo;
}
function svg(etiqueta, attrs, ...hijos) {
  const nodo = document.createElementNS("http://www.w3.org/2000/svg", etiqueta);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") nodo.textContent = v; else if (v !== null && v !== undefined) nodo.setAttribute(k, v);
  }
  for (const h of hijos.flat()) if (h) nodo.append(h);
  return nodo;
}
const pct = (x) => (x === null || isNaN(x) ? "–" : Math.round(x * 100) + "%");
const num = (x, d = 2) => (x === null || x === undefined ? "–" : x.toFixed(d));
function duracion(s) {
  if (s === null || s === undefined) return "–";
  if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + " s";
  const m = Math.floor(s / 60); return m + " min " + Math.round(s - m * 60) + " s";
}
function fecha(iso, corta) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("es-MX", corta ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function estadoDe(caso) {
  const conocido = (META[caso.id] || {}).xfail;
  if (caso.exito) return conocido ? "xpass" : "paso";
  return conocido ? "conocido" : "fallo";
}
const errorJuez = (caso) => caso.metricas.some((m) => m.error);
function puntajeMin(caso) {
  const v = caso.metricas.map((m) => m.puntaje).filter((p) => p !== null);
  return v.length ? Math.min(...v) : null;
}
function resumenDe(corrida) {
  const total = corrida.casos.length;
  const ok = corrida.casos.filter((c) => c.exito).length;
  return { total, ok, tasa: total ? ok / total : null };
}
const indice = (corrida) => new Map(corrida.casos.map((c) => [c.id, c]));

// ---------- tooltip
const tip = $("tip");
function conTip(nodo, texto) {
  nodo.setAttribute("tabindex", "0");
  nodo.setAttribute("aria-label", texto.replace(/\n/g, ". "));
  const mostrar = (x, y) => {
    tip.textContent = ""; texto.split("\n").forEach((l, i) => tip.append(i ? el("div", { text: l }) : el("b", { text: l })));
    tip.style.opacity = 1;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.max(6, Math.min(x + 12, innerWidth - r.width - 6)) + "px";
    tip.style.top = Math.max(6, y - r.height - 10) + "px";
  };
  nodo.addEventListener("mousemove", (e) => mostrar(e.clientX, e.clientY));
  nodo.addEventListener("focus", () => { const r = nodo.getBoundingClientRect(); mostrar(r.left + r.width / 2, r.top); });
  nodo.addEventListener("mouseleave", () => (tip.style.opacity = 0));
  nodo.addEventListener("blur", () => (tip.style.opacity = 0));
  return nodo;
}

// ---------- gráficas
function barras(contenedor, filas, opciones) {
  // filas: [{rotulo, valor (0..1), texto, tip}]
  contenedor.textContent = "";
  if (!filas.length) { contenedor.append(el("div", { class: "vacio", text: "Sin datos en esta corrida." })); return; }
  const ancho = Math.max(300, contenedor.clientWidth || 520), alto = 26, izq = Math.min(190, 14 + 6.6 * Math.max(...filas.map((f) => f.rotulo.length)));
  const der = opciones.der || 56, util = ancho - izq - der;
  const g = svg("svg", { viewBox: `0 0 ${ancho} ${filas.length * alto + 18}`, role: "img", "aria-label": opciones.titulo });
  for (const t of [0, 0.5, 1]) {
    const x = izq + util * t;
    g.append(svg("line", { x1: x, x2: x, y1: 0, y2: filas.length * alto, stroke: "var(--rejilla)", "stroke-width": 1 }));
    g.append(svg("text", { x, y: filas.length * alto + 14, "text-anchor": "middle", text: opciones.formato(t) }));
  }
  filas.forEach((f, i) => {
    const y = i * alto + 6, w = Math.max(f.valor === null ? 0 : util * f.valor, 0);
    const grupo = svg("g", { class: "barra" });
    grupo.append(svg("rect", { x: 0, y: i * alto, width: ancho, height: alto, fill: "transparent" }));
    grupo.append(svg("text", { x: izq - 8, y: y + 11, "text-anchor": "end", text: f.rotulo.length > 26 ? f.rotulo.slice(0, 25) + "…" : f.rotulo }));
    if (w > 0) grupo.append(svg("path", { class: "dato", fill: "var(--serie)",
      d: `M${izq},${y} h${Math.max(w - 4, 0)} a4,4 0 0 1 4,4 v6 a4,4 0 0 1 -4,4 h${-Math.max(w - 4, 0)} z` }));
    grupo.append(svg("text", { class: "valor", x: izq + w + 6, y: y + 11, text: f.texto }));
    conTip(grupo, f.tip);
    g.append(grupo);
  });
  g.append(svg("line", { x1: izq, x2: izq, y1: 0, y2: filas.length * alto, stroke: "var(--eje)", "stroke-width": 1 }));
  contenedor.append(g);
}

function graficaEtiquetas(corrida) {
  const grupos = new Map();
  for (const c of corrida.casos) for (const e of (c.etiquetas.length ? c.etiquetas : ["(sin etiqueta)"])) {
    const g = grupos.get(e) || { total: 0, ok: 0 }; g.total++; if (c.exito) g.ok++; grupos.set(e, g);
  }
  const filas = [...grupos].map(([rotulo, g]) => ({ rotulo, valor: g.ok / g.total, texto: `${pct(g.ok / g.total)}  ${g.ok}/${g.total}`,
    tip: `${rotulo}\n${g.ok} de ${g.total} casos aprobados (${pct(g.ok / g.total)})` }))
    .sort((a, b) => a.valor - b.valor || a.rotulo.localeCompare(b.rotulo));
  barras($("gEtiquetas"), filas, { titulo: "Aprobación por etiqueta", formato: pct, der: 84 });
}

function graficaMetricas(corrida) {
  const grupos = new Map();
  for (const c of corrida.casos) for (const m of c.metricas) {
    const g = grupos.get(m.nombre) || { n: 0, suma: 0, conPuntaje: 0, ok: 0, errores: 0 };
    g.n++; if (m.exito) g.ok++; if (m.error) g.errores++;
    if (m.puntaje !== null) { g.suma += m.puntaje; g.conPuntaje++; }
    grupos.set(m.nombre, g);
  }
  const filas = [...grupos].map(([rotulo, g]) => {
    const prom = g.conPuntaje ? g.suma / g.conPuntaje : null;
    return { rotulo, valor: prom, texto: `${num(prom)}  ${g.ok}/${g.n}`,
      tip: `${rotulo}\nPuntaje promedio: ${num(prom)}\nSuperan el umbral: ${g.ok} de ${g.n}` + (g.errores ? `\nErrores del juez: ${g.errores}` : "") };
  }).sort((a, b) => (a.valor ?? -1) - (b.valor ?? -1));
  barras($("gMetricas"), filas, { titulo: "Puntaje promedio por métrica", formato: (t) => t.toFixed(1), der: 84 });
}

function graficaHistorico() {
  const cont = $("gHistorico"); cont.textContent = "";
  if (CORRIDAS.length < 2) { cont.append(el("div", { class: "vacio", text: "Se necesita más de una corrida para ver la evolución." })); return; }
  const ancho = Math.max(300, cont.clientWidth || 520), alto = 210, izq = 38, der = 16, sup = 12, inf = 44, w = ancho - izq - der, h = alto - sup - inf;
  const pts = CORRIDAS.map((c, i) => ({ i, r: resumenDe(c), c }));
  const x = (i) => izq + (CORRIDAS.length === 1 ? w / 2 : (w * i) / (CORRIDAS.length - 1));
  const y = (v) => sup + h * (1 - v);
  const g = svg("svg", { viewBox: `0 0 ${ancho} ${alto}`, role: "img", "aria-label": "Aprobación por corrida" });
  for (const t of [0, 0.5, 1]) {
    g.append(svg("line", { x1: izq, x2: ancho - der, y1: y(t), y2: y(t), stroke: t ? "var(--rejilla)" : "var(--eje)", "stroke-width": 1 }));
    g.append(svg("text", { x: izq - 6, y: y(t) + 4, "text-anchor": "end", text: pct(t) }));
  }
  g.append(svg("path", { d: pts.map((p, k) => `${k ? "L" : "M"}${x(p.i)},${y(p.r.tasa ?? 0)}`).join(" "), fill: "none", stroke: "var(--serie)", "stroke-width": 2, "stroke-linejoin": "round" }));
  const paso = Math.max(1, Math.ceil(CORRIDAS.length / 4));
  pts.forEach((p) => {
    const sel = p.i === estado.corrida;
    if (p.i % paso === 0 || p.i === CORRIDAS.length - 1) {
      const d = new Date(p.c.fecha), ancla = p.i === 0 ? "start" : p.i === CORRIDAS.length - 1 ? "end" : "middle";
      g.append(svg("text", { x: x(p.i), y: alto - 20, "text-anchor": ancla, text: d.toLocaleDateString("es-MX", { day: "2-digit", month: "short" }) }));
      g.append(svg("text", { x: x(p.i), y: alto - 6, "text-anchor": ancla, text: d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" }) }));
    }
    const punto = svg("g", { class: "barra", style: "cursor:pointer" },
      svg("circle", { cx: x(p.i), cy: y(p.r.tasa ?? 0), r: 14, fill: "transparent" }),
      svg("circle", { class: "dato", cx: x(p.i), cy: y(p.r.tasa ?? 0), r: sel ? 6 : 4.5, fill: sel ? "var(--tinta)" : "var(--serie)", stroke: "var(--sup)", "stroke-width": 2 }));
    conTip(punto, `${fecha(p.c.fecha)}\n${p.r.ok} de ${p.r.total} casos aprobados (${pct(p.r.tasa)})`);
    const abrir = () => { estado.corrida = p.i; estado.base = p.i - 1; estado.abiertos.clear(); pintar(); };
    punto.addEventListener("click", abrir);
    punto.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); abrir(); } });
    g.append(punto);
  });
  cont.append(g);
}

// ---------- cabecera, indicadores y cambios
function llenarSelectores() {
  const sc = $("selCorrida"), sb = $("selBase");
  sc.textContent = ""; sb.textContent = "";
  sb.append(el("option", { value: -1, text: "Sin comparación" }));
  [...CORRIDAS.keys()].reverse().forEach((i) => {
    const r = resumenDe(CORRIDAS[i]);
    const texto = `${fecha(CORRIDAS[i].fecha)} · ${r.total} casos · ${pct(r.tasa)}`;
    sc.append(el("option", { value: i, text: texto }));
    if (i !== estado.corrida) sb.append(el("option", { value: i, text: texto }));
  });
  sc.value = estado.corrida; sb.value = estado.base;
  if (sb.value === "") { estado.base = -1; sb.value = -1; }
}

function comparar(corrida, base) {
  // Devuelve por id: "regresion" | "corregido" | "igual" | "nuevo"
  const cambios = new Map();
  if (!base) return cambios;
  const antes = indice(base);
  for (const c of corrida.casos) {
    const previo = antes.get(c.id);
    cambios.set(c.id, !previo ? "nuevo" : previo.exito && !c.exito ? "regresion" : !previo.exito && c.exito ? "corregido" : "igual");
  }
  return cambios;
}

function pintarKpis(corrida, base, cambios) {
  const r = resumenDe(corrida), cont = $("kpis"); cont.textContent = "";
  const cuenta = (e) => corrida.casos.filter((c) => estadoDe(c) === e).length;
  const conocidos = cuenta("conocido"), xpass = cuenta("xpass"), fallos = cuenta("fallo");
  const sinConocidos = r.total - conocidos ? r.ok / (r.total - conocidos) : null;
  const juez = corrida.casos.filter(errorJuez).length;
  let notaTasa = conocidos ? `${pct(sinConocidos)} sin contar fallos conocidos` : "";
  let claseTasa = "";
  if (base) {
    // Solo casos presentes en ambas corridas, para comparar lo mismo con lo mismo.
    const antes = indice(base), comunes = corrida.casos.filter((c) => antes.has(c.id));
    if (comunes.length) {
      const hoy = comunes.filter((c) => c.exito).length / comunes.length;
      const ayer = comunes.filter((c) => antes.get(c.id).exito).length / comunes.length;
      const d = Math.round((hoy - ayer) * 100);
      notaTasa = d === 0 ? `Sin cambio en ${comunes.length} casos comunes` : `${d > 0 ? "▲ +" : "▼ "}${d} pts en ${comunes.length} casos comunes`;
      claseTasa = d > 0 ? "sube" : d < 0 ? "baja" : "";
    }
  }
  const regresiones = [...cambios.values()].filter((v) => v === "regresion").length;
  const tarjetas = [
    ["Aprobación", pct(r.tasa), notaTasa, claseTasa],
    ["Aprobados", `${r.ok} de ${r.total}`, xpass ? `${xpass} marcados como fallo conocido ya pasan` : "", ""],
    ["Fallidos", String(fallos), base ? (regresiones ? `${regresiones} pasaban en la corrida anterior` : "Ninguno pasaba antes") : "", regresiones ? "baja" : ""],
    ["Fallos conocidos", String(conocidos), conocidos ? "Marcados con xfail en los YAML" : "", ""],
    ["Errores del juez", String(juez), juez ? "Casos donde una métrica no pudo calcularse" : "", juez ? "baja" : ""],
    ["Duración", duracion(corrida.duracion), "", ""],
  ];
  for (const [rotulo, valor, nota, clase] of tarjetas)
    cont.append(el("div", { class: "tarjeta kpi" }, el("div", { class: "rotulo", text: rotulo }), el("div", { class: "valor", text: valor }), el("div", { class: "nota " + clase, text: nota })));
}

function pintarCambios(corrida, base, cambios) {
  const cont = $("cambios"); cont.textContent = "";
  if (!base) { cont.hidden = true; return; }
  cont.hidden = false;
  const grupos = { regresion: [], corregido: [], nuevo: [] };
  for (const [id, tipo] of cambios) if (grupos[tipo]) grupos[tipo].push(id);
  const ahora = new Set(corrida.casos.map((c) => c.id));
  const ausentes = base.casos.filter((c) => !ahora.has(c.id)).length;
  cont.append(el("h2", { text: "Cambios frente a la corrida del " + fecha(base.fecha) }));
  if (!grupos.regresion.length && !grupos.corregido.length && !grupos.nuevo.length)
    cont.append(el("p", { class: "sub", style: "margin:4px 0 0", text: "Ningún caso cambió de resultado." }));
  const fila = (titulo, ids) => ids.length && cont.append(el("div", { class: "fila" }, el("b", { text: `${titulo} (${ids.length})` }),
    ids.map((id) => el("button", { class: "ficha", text: id, onclick: () => irA(id) }))));
  fila("✕ Dejaron de pasar", grupos.regresion);
  fila("✓ Ahora pasan", grupos.corregido);
  fila("＋ Casos nuevos", grupos.nuevo);
  if (ausentes) cont.append(el("p", { class: "sub", style: "margin:8px 0 0", text: `${ausentes} casos de la corrida anterior no se ejecutaron en esta.` }));
}

function irA(id) {
  estado.filtro = "todos"; estado.etiqueta = ""; estado.buscar = ""; $("fBuscar").value = "";
  estado.abiertos.add(id); pintar();
  const fila = document.querySelector(`tr.caso[data-id="${CSS.escape(id)}"]`);
  if (fila) { fila.scrollIntoView({ block: "center", behavior: "smooth" }); fila.focus({ preventScroll: true }); }
}

// ---------- tabla
function fichaEstado(caso) {
  const e = estadoDe(caso), def = ESTADOS[e];
  return el("span", { class: "estado" }, el("span", { class: "punto " + e, text: def.icono, "aria-hidden": "true" }), def.rotulo);
}
function fichaMetrica(m) {
  const clase = m.error ? "err" : m.exito ? "ok" : "no";
  return el("span", { class: "met " + clase, title: m.error ? "El juez no pudo calcular esta métrica" : `Umbral ${num(m.umbral)}` },
    el("i", { text: m.error ? "⚠" : m.exito ? "✓" : "✕" }), m.nombre.replace(" [GEval]", ""), el("b", { text: m.error && m.puntaje === null ? "error" : num(m.puntaje) }));
}
const CAMBIOS = { regresion: ["✕ Dejó de pasar", "baja"], corregido: ["✓ Ahora pasa", "sube"], nuevo: ["＋ Nuevo", ""], igual: ["", ""] };

function bloque(titulo, contenido) { return el("div", {}, el("h3", { text: titulo }), contenido); }
function detalle(caso, previo) {
  const meta = META[caso.id] || {};
  const izq = el("div", {},
    bloque("Pregunta", el("pre", { text: caso.pregunta || "(vacía)" })),
    el("div", { style: "height:10px" }),
    bloque("Respuesta del agente", el("pre", { text: caso.respuesta || "(sin respuesta)" })),
    caso.esperada && el("div", { style: "margin-top:10px" }, bloque("Respuesta esperada", el("pre", { text: caso.esperada }))),
    meta.criterio && el("div", { style: "margin-top:10px" }, bloque("Criterio del caso", el("pre", { text: meta.criterio }))),
    previo && previo.respuesta !== caso.respuesta && el("details", { style: "margin-top:10px" }, el("summary", { text: "Respuesta en la corrida anterior", style: "cursor:pointer;font-size:12.5px;color:var(--tinta2)" }), el("pre", { text: previo.respuesta || "(sin respuesta)", style: "margin-top:6px" })),
    caso.contexto.length > 0 && el("details", { style: "margin-top:10px" }, el("summary", { text: `Evidencia de herramientas (${caso.contexto.length})`, style: "cursor:pointer;font-size:12.5px;color:var(--tinta2)" }), caso.contexto.map((c) => el("pre", { text: c, style: "margin-top:6px" }))));
  const antes = new Map((previo ? previo.metricas : []).map((m) => [m.nombre, m]));
  const der = el("div", {},
    bloque("Métricas", caso.metricas.length ? caso.metricas.map((m) => {
      const p = antes.get(m.nombre);
      const delta = p && p.puntaje !== null && m.puntaje !== null && Math.abs(m.puntaje - p.puntaje) >= 0.005 ? m.puntaje - p.puntaje : null;
      return el("div", { class: "mdet" },
        el("div", { class: "cab" }, el("span", {}, (m.error ? "⚠ " : m.exito ? "✓ " : "✕ ") + m.nombre),
          el("span", {}, `${num(m.puntaje)} / umbral ${num(m.umbral)}`, delta !== null && el("span", { class: delta > 0 ? "sube" : "baja", text: `  ${delta > 0 ? "▲ +" : "▼ "}${delta.toFixed(2)}` }))),
        el("div", { class: "escala" }, el("span", { style: `width:${Math.max(0, Math.min(1, m.puntaje ?? 0)) * 100}%` }), m.umbral !== null && el("em", { style: `left:calc(${Math.max(0, Math.min(1, m.umbral)) * 100}% - 1px)`, title: "Umbral" })),
        m.razon && el("p", { text: m.razon }),
        m.error && el("p", { class: "err", text: "Error del juez: " + m.error }),
        m.modelo && el("p", { text: "Juez: " + m.modelo, style: "font-size:11.5px;margin-top:4px" }));
    }) : el("p", { class: "sub", text: "Este caso no registró métricas." })),
    el("div", { style: "margin-top:10px" }, bloque("Herramientas", el("pre", { text:
      "Llamadas:  " + (caso.herramientas.join(", ") || "ninguna") +
      (caso.herramientas_esperadas.length ? "\nEsperadas: " + caso.herramientas_esperadas.join(", ") : "") }))),
    meta.xfail && el("div", { style: "margin-top:10px" }, bloque("Fallo conocido", el("pre", { text: meta.xfail }))),
    (meta.origen || meta.rol) && el("p", { class: "sub", style: "margin:10px 0 0", text: [meta.origen && `Archivo: casos/${meta.origen}.yaml`, meta.rol && `Rol: ${meta.rol}`].filter(Boolean).join(" · ") }));
  return el("div", { class: "dgrid" }, izq, der);
}

function pintarTabla(corrida, base, cambios) {
  const antes = base ? indice(base) : new Map();
  const peso = { fallo: 0, xpass: 1, conocido: 2, paso: 3 };
  const q = estado.buscar.trim().toLowerCase();
  let casos = corrida.casos.filter((c) => {
    const e = estadoDe(c);
    if (estado.filtro === "fallo" && e !== "fallo") return false;
    if (estado.filtro === "paso" && !c.exito) return false;
    if (estado.filtro === "conocido" && e !== "conocido" && e !== "xpass") return false;
    if (estado.filtro === "cambio" && !["regresion", "corregido"].includes(cambios.get(c.id))) return false;
    if (estado.filtro === "juez" && !errorJuez(c)) return false;
    if (estado.etiqueta && !c.etiquetas.includes(estado.etiqueta)) return false;
    if (q && !(c.id + "\n" + c.pregunta + "\n" + c.respuesta).toLowerCase().includes(q)) return false;
    return true;
  });
  const orden = {
    estado: (a, b) => (cambios.get(b.id) === "regresion") - (cambios.get(a.id) === "regresion") || peso[estadoDe(a)] - peso[estadoDe(b)] || a.id.localeCompare(b.id),
    id: (a, b) => a.id.localeCompare(b.id),
    puntaje: (a, b) => (puntajeMin(a) ?? 2) - (puntajeMin(b) ?? 2) || a.id.localeCompare(b.id),
    tiempo: (a, b) => (b.duracion ?? -1) - (a.duracion ?? -1),
  }[estado.orden];
  casos = casos.slice().sort(orden);
  const cuerpo = $("cuerpo"); cuerpo.textContent = "";
  for (const c of casos) {
    const abierto = estado.abiertos.has(c.id), cambio = CAMBIOS[cambios.get(c.id)];
    const alternar = () => { abierto ? estado.abiertos.delete(c.id) : estado.abiertos.add(c.id); pintarTabla(corrida, base, cambios); };
    cuerpo.append(el("tr", { class: "caso", "data-id": c.id, tabindex: 0, "aria-expanded": String(abierto), onclick: alternar,
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); alternar(); } } },
      el("td", {}, fichaEstado(c)),
      el("td", {}, el("div", { class: "id", text: (abierto ? "▾ " : "▸ ") + c.id }), el("div", { class: "pregunta", text: c.pregunta })),
      el("td", {}, c.etiquetas.map((e) => el("span", { class: "etq", text: e }))),
      el("td", {}, c.metricas.map(fichaMetrica)),
      el("td", { class: cambio ? cambio[1] : "", style: "white-space:nowrap;font-size:12.5px", text: cambio ? cambio[0] : "" }),
      el("td", { class: "num", text: duracion(c.duracion) })));
    if (abierto) cuerpo.append(el("tr", { class: "detalle" }, el("td", { colspan: 6 }, detalle(c, antes.get(c.id)))));
  }
  $("vacio").hidden = casos.length > 0;
}

function pintarFiltros(corrida, cambios) {
  const cuenta = (f) => corrida.casos.filter(f).length;
  const opciones = [
    ["todos", "Todos", corrida.casos.length],
    ["fallo", "Fallidos", cuenta((c) => estadoDe(c) === "fallo")],
    ["paso", "Aprobados", cuenta((c) => c.exito)],
    ["conocido", "Fallos conocidos", cuenta((c) => ["conocido", "xpass"].includes(estadoDe(c)))],
    ["cambio", "Cambiaron", cuenta((c) => ["regresion", "corregido"].includes(cambios.get(c.id)))],
    ["juez", "Error del juez", cuenta(errorJuez)],
  ];
  const cont = $("fEstado"); cont.textContent = "";
  for (const [valor, rotulo, n] of opciones) {
    if (!n && !["todos", "fallo", "paso"].includes(valor)) continue;
    cont.append(el("button", { type: "button", "aria-pressed": String(estado.filtro === valor), text: `${rotulo} ${n}`, onclick: () => { estado.filtro = valor; pintar(); } }));
  }
  const etiquetas = [...new Set(corrida.casos.flatMap((c) => c.etiquetas))].sort();
  const sel = $("fEtiqueta"); sel.textContent = "";
  sel.append(el("option", { value: "", text: "Todas" }), etiquetas.map((e) => el("option", { value: e, text: e })));
  if (!etiquetas.includes(estado.etiqueta)) estado.etiqueta = "";
  sel.value = estado.etiqueta;
}

function pintar() {
  const corrida = CORRIDAS[estado.corrida];
  if (estado.base === estado.corrida) estado.base = -1;
  const base = estado.base >= 0 ? CORRIDAS[estado.base] : null;
  const cambios = comparar(corrida, base);
  llenarSelectores();
  const r = resumenDe(corrida);
  $("resumen").textContent = `${fecha(corrida.fecha)} · ${r.total} casos · ${corrida.archivo}`;
  pintarKpis(corrida, base, cambios);
  pintarCambios(corrida, base, cambios);
  graficaEtiquetas(corrida); graficaMetricas(corrida); graficaHistorico();
  pintarFiltros(corrida, cambios);
  pintarTabla(corrida, base, cambios);
  $("pie").textContent = `Generado el ${fecha(DATOS.generado)} a partir de ${CORRIDAS.length} corrida(s). Los resultados son los que DeepEval guardó en disco; este archivo no se conecta a internet.`;
}

$("selCorrida").addEventListener("change", (e) => { estado.corrida = +e.target.value; estado.base = estado.corrida - 1; estado.abiertos.clear(); pintar(); });
$("selBase").addEventListener("change", (e) => { estado.base = +e.target.value; pintar(); });
$("fEtiqueta").addEventListener("change", (e) => { estado.etiqueta = e.target.value; pintar(); });
$("fOrden").addEventListener("change", (e) => { estado.orden = e.target.value; pintar(); });
$("fBuscar").addEventListener("input", (e) => { estado.buscar = e.target.value; const c = CORRIDAS[estado.corrida], b = estado.base >= 0 ? CORRIDAS[estado.base] : null; pintarTabla(c, b, comparar(c, b)); });
let _redim; addEventListener("resize", () => { clearTimeout(_redim); _redim = setTimeout(() => { const c = CORRIDAS[estado.corrida]; graficaEtiquetas(c); graficaMetricas(c); graficaHistorico(); }, 150); });
pintar();
// Enlace directo a un caso: reporte.html#id-del-caso
if (location.hash.length > 1) { const id = decodeURIComponent(location.hash.slice(1)); if (CORRIDAS[estado.corrida].casos.some((c) => c.id === id)) irA(id); }
</script>
</body>
</html>
"""


def generar_html(corridas: list[dict], meta: dict) -> str:
    datos = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "corridas": corridas,
        "meta": meta,
    }
    # "</" escapado para que ningún texto del agente cierre la etiqueta <script>.
    incrustado = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")
    return PLANTILLA.replace("__DATOS__", incrustado)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reporte HTML local de resultados de DeepEval.")
    parser.add_argument(
        "carpeta",
        nargs="?",
        help="Carpeta con los test_run_*.json, o un archivo JSON. "
        "Por defecto: DEEPEVAL_RESULTS_FOLDER o deepeval/resultados.",
    )
    parser.add_argument("--salida", help="Ruta del HTML. Por defecto: <carpeta>/reporte.html")
    parser.add_argument(
        "--ultimas", type=int, default=30, help="Número de corridas más recientes a incluir (30)."
    )
    parser.add_argument("--abrir", action="store_true", help="Abre el reporte en el navegador.")
    args = parser.parse_args()

    carpeta = Path(args.carpeta).expanduser() if args.carpeta else carpeta_por_defecto()
    if not carpeta.exists():
        print(
            f"No existe {carpeta}.\nDefine DEEPEVAL_RESULTS_FOLDER=deepeval/resultados en .env, "
            "ejecuta la suite y vuelve a intentar.",
            file=sys.stderr,
        )
        return 1

    archivos = buscar_archivos(carpeta)
    corridas = [c for c in (leer_corrida(a) for a in archivos) if c]
    corridas.sort(key=lambda c: c["fecha"])
    corridas = corridas[-max(1, args.ultimas):]
    if not corridas:
        print(f"No se encontraron resultados de DeepEval en {carpeta}.", file=sys.stderr)
        return 1

    destino = carpeta if carpeta.is_dir() else carpeta.parent
    salida = Path(args.salida).expanduser() if args.salida else destino / "reporte.html"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(generar_html(corridas, metadatos_yaml()), encoding="utf-8")

    ultima = corridas[-1]
    aprobados = sum(1 for c in ultima["casos"] if c["exito"])
    print(f"Corridas incluidas: {len(corridas)}")
    print(f"Última corrida:     {aprobados}/{len(ultima['casos'])} casos aprobados")
    print(f"Reporte:            {salida.resolve()}")
    if args.abrir:
        webbrowser.open(salida.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
