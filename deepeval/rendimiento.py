"""Rendimiento de la suite: equipo, tiempos, recursos y tokens por caso.

Además de las métricas de calidad del agente, cada corrida registra:

- **Equipo** (una vez): sistema operativo, CPU, núcleos, RAM, GPU, energía,
  versión de Ollama y modelos cargados, versiones de paquetes y commit de Git.
- **Por caso**: tiempo del agente (turno final y total con turnos previos),
  tiempo reportado por el servicio y por paso, tiempo del juez, CPU del
  proceso y de los subprocesos MCP, picos de memoria (pytest + MCP, Ollama y
  sistema), uso de GPU NVIDIA y tokens/latencia de cada llamada a Ollama.
- **Resumen** (al final): ``resultados/rendimiento_<fecha>.json`` y ``.md``
  con percentiles por historia, por modelo y la matriz de cobertura.

Todo es local: nada sale de la máquina. El nombre del equipo no se guarda;
se usa un identificador anónimo (hash corto) para distinguir equipos.

``psutil`` es opcional (grupo dev). Sin él se registran tiempos, CPU vía
``os.times`` y el pico de memoria de ``resource`` cuando existe.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata as importlib_metadata
from pathlib import Path

try:
    import psutil  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dependencia opcional
    psutil = None

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
CARPETA_HOOK = AQUI / "_subproceso"
VARIABLE_LLM = "DEEPEVAL_LLM_STATS_FILE"
_MB = 1024 * 1024
PAQUETES = (
    "deepeval",
    "langchain",
    "langchain-core",
    "langchain-ollama",
    "langgraph",
    "fastmcp",
    "markitdown",
    "pinecone",
    "sqlglot",
    "streamlit",
)


def activo() -> bool:
    return os.getenv("DEEPEVAL_RENDIMIENTO", "1").strip().lower() not in {"0", "false", "no"}


# --------------------------------------------------------------- utilidades


def _comando(args: list[str], timeout: float = 5.0) -> str:
    if not shutil.which(args[0]):
        return ""
    try:
        salida = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (salida.stdout or "").strip()


def _ollama_host() -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def _ollama_get(ruta: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(_ollama_host() + ruta, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - Ollama puede no estar activo
        return None


def ollama_modelos_cargados() -> list[dict]:
    """Snapshot de /api/ps: qué modelos están en memoria y cuánto en GPU."""
    datos = _ollama_get("/api/ps") or {}
    modelos = []
    for modelo in datos.get("models") or []:
        tamano = modelo.get("size") or 0
        vram = modelo.get("size_vram") or 0
        modelos.append({
            "modelo": modelo.get("name") or modelo.get("model"),
            "tamano_mb": round(tamano / _MB, 1) if tamano else None,
            "vram_mb": round(vram / _MB, 1) if vram else 0.0,
            "porcentaje_gpu": round(100 * vram / tamano, 1) if tamano else None,
            "contexto": modelo.get("context_length"),
        })
    return modelos


def _percentil(valores: list[float], p: float) -> float | None:
    datos = sorted(v for v in valores if v is not None)
    if not datos:
        return None
    if len(datos) == 1:
        return round(datos[0], 3)
    k = (len(datos) - 1) * p
    inferior, superior = int(k), min(int(k) + 1, len(datos) - 1)
    return round(datos[inferior] + (datos[superior] - datos[inferior]) * (k - inferior), 3)


def resumen_numerico(valores: list[float]) -> dict:
    datos = [v for v in valores if v is not None]
    if not datos:
        return {"n": 0}
    return {
        "n": len(datos),
        "total": round(sum(datos), 3),
        "media": round(statistics.fmean(datos), 3),
        "p50": _percentil(datos, 0.50),
        "p90": _percentil(datos, 0.90),
        "p95": _percentil(datos, 0.95),
        "max": round(max(datos), 3),
        "min": round(min(datos), 3),
    }


# ------------------------------------------------------------------ equipo


def _cpu_modelo() -> str:
    sistema = platform.system()
    if sistema == "Linux":
        try:
            for linea in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if linea.lower().startswith(("model name", "hardware", "cpu model")):
                    return linea.split(":", 1)[1].strip()
        except OSError:
            pass
    elif sistema == "Darwin":
        nombre = _comando(["sysctl", "-n", "machdep.cpu.brand_string"])
        if nombre:
            return nombre
    elif sistema == "Windows":
        try:
            import winreg  # type: ignore[import-not-found]

            clave = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            return str(winreg.QueryValueEx(clave, "ProcessorNameString")[0]).strip()
        except Exception:  # noqa: BLE001
            pass
    return platform.processor() or platform.machine()


def _nucleos_fisicos() -> int | None:
    if psutil is not None:
        try:
            return psutil.cpu_count(logical=False)
        except Exception:  # noqa: BLE001
            pass
    if platform.system() == "Darwin":
        valor = _comando(["sysctl", "-n", "hw.physicalcpu"])
        return int(valor) if valor.isdigit() else None
    return None


def _ram_total_gb() -> float | None:
    if psutil is not None:
        return round(psutil.virtual_memory().total / 1024**3, 1)
    sistema = platform.system()
    try:
        if sistema == "Linux":
            for linea in Path("/proc/meminfo").read_text().splitlines():
                if linea.startswith("MemTotal:"):
                    return round(int(linea.split()[1]) / 1024**2, 1)
        if sistema == "Darwin":
            return round(int(_comando(["sysctl", "-n", "hw.memsize"])) / 1024**3, 1)
        if sistema == "Windows":
            import ctypes

            class _Memoria(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            memoria = _Memoria()
            memoria.dwLength = ctypes.sizeof(_Memoria)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memoria))  # type: ignore[attr-defined]
            return round(memoria.ullTotalPhys / 1024**3, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def _gpus() -> list[dict]:
    salida = _comando([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if salida:
        gpus = []
        for linea in salida.splitlines():
            partes = [p.strip() for p in linea.split(",")]
            if len(partes) >= 3:
                gpus.append({
                    "nombre": partes[0],
                    "vram_mb": float(partes[1]) if partes[1].replace(".", "").isdigit() else None,
                    "driver": partes[2],
                    "fuente": "nvidia-smi",
                })
        if gpus:
            return gpus
    sistema = platform.system()
    if sistema == "Darwin" and platform.machine() == "arm64":
        return [{
            "nombre": _cpu_modelo() + " (GPU integrada)",
            "vram_mb": None,
            "nota": "Apple Silicon: memoria unificada compartida con la RAM",
            "fuente": "sysctl",
        }]
    if sistema == "Windows":
        salida = _comando([
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | "
            "ForEach-Object { $_.Name + '|' + $_.AdapterRAM + '|' + $_.DriverVersion }",
        ], timeout=10)
        gpus = []
        for linea in salida.splitlines():
            nombre, _, resto = linea.partition("|")
            ram, _, driver = resto.partition("|")
            if nombre.strip():
                gpus.append({
                    "nombre": nombre.strip(),
                    # AdapterRAM es de 32 bits: satura en 4 GB.
                    "vram_mb": round(int(ram) / _MB) if ram.strip().isdigit() else None,
                    "driver": driver.strip() or None,
                    "fuente": "Win32_VideoController",
                })
        return gpus
    if sistema == "Linux":
        salida = _comando(["lspci"])
        return [
            {"nombre": linea.split(":", 2)[-1].strip(), "fuente": "lspci"}
            for linea in salida.splitlines()
            if "VGA" in linea or "3D controller" in linea
        ]
    return []


def _energia() -> dict | None:
    if psutil is None or not hasattr(psutil, "sensors_battery"):
        return None
    try:
        bateria = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None
    if bateria is None:
        return {"bateria": False}
    return {
        "bateria": True,
        "conectado": bool(bateria.power_plugged),
        "porcentaje": round(bateria.percent, 1),
        "nota": "En batería el CPU/GPU puede reducir frecuencia y alterar tiempos.",
    }


def _git() -> dict:
    commit = _comando(["git", "-C", str(RAIZ), "rev-parse", "--short", "HEAD"])
    if not commit:
        return {}
    cambios = _comando(["git", "-C", str(RAIZ), "status", "--porcelain"])
    rama = _comando(["git", "-C", str(RAIZ), "rev-parse", "--abbrev-ref", "HEAD"])
    return {"commit": commit, "rama": rama or None, "cambios_sin_commit": bool(cambios)}


def _paquetes() -> dict:
    versiones = {}
    for paquete in PAQUETES:
        try:
            versiones[paquete] = importlib_metadata.version(paquete)
        except importlib_metadata.PackageNotFoundError:
            continue
    return versiones


def _modelos_configurados() -> dict:
    try:
        import yaml

        datos = yaml.safe_load(
            (RAIZ / "proyecto" / "config" / "models.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:  # noqa: BLE001
        return {}
    modelos = {
        nombre: (config or {}).get("model")
        for nombre, config in (datos.get("agents") or {}).items()
    }
    if isinstance(datos.get("document_pdf"), dict):
        modelos["document_pdf"] = datos["document_pdf"].get("model")
    for variable, clave in (
        ("DOCUMENT_INTELLIGENCE_MODEL", "document_intelligence"),
        ("DOCUMENT_PDF_MODEL", "document_pdf"),
        ("DOCUMENT_RELEVANCE_MODEL", "document_relevance"),
    ):
        if os.getenv(variable, "").strip():
            modelos[clave] = os.environ[variable].strip()
    return modelos


def dispositivo() -> dict:
    """Especificaciones del equipo y del entorno de la corrida."""
    version_ollama = (_ollama_get("/api/version") or {}).get("version")
    return {
        "equipo_id": hashlib.sha256(platform.node().encode()).hexdigest()[:10],
        "sistema": {
            "so": platform.system(),
            "version": platform.release(),
            "detalle": platform.version()[:120],
            "arquitectura": platform.machine(),
        },
        "cpu": {
            "modelo": _cpu_modelo(),
            "nucleos_fisicos": _nucleos_fisicos(),
            "nucleos_logicos": os.cpu_count(),
        },
        "ram_gb": _ram_total_gb(),
        "gpus": _gpus(),
        "energia": _energia(),
        "python": platform.python_version(),
        "paquetes": _paquetes(),
        "ollama": {
            "host_local": _ollama_host().split("//", 1)[-1].startswith(("localhost", "127.")),
            "version": version_ollama,
            "disponible": version_ollama is not None,
            "modelos_configurados": _modelos_configurados(),
            "modelos_cargados_al_inicio": ollama_modelos_cargados(),
        },
        "juez": os.getenv("DEEPEVAL_JUEZ", "ollama:qwen2.5:7b-instruct"),
        "solo_reglas": os.getenv("DEEPEVAL_SOLO_REGLAS", "").strip().lower() in {"1", "true", "si", "sí"},
        "configuracion": {
            clave: os.getenv(clave)
            for clave in (
                "ORCHESTRATION_PLANNER",
                "ORCHESTRATION_SYNTHESIS",
                "MCP_TIMEOUT",
                "RAG_CANDIDATES",
                "DATA_ANALYST_METADATA_CACHE_TTL",
                "PROYECTO_OLLAMA_MMAP",
            )
            if os.getenv(clave) is not None
        },
        "git": _git(),
        "psutil": psutil is not None,
    }


# ------------------------------------------------------------- muestreo


def _procesos_ollama() -> list:
    if psutil is None:
        return []
    procesos = []
    for proceso in psutil.process_iter(["name"]):
        nombre = (proceso.info.get("name") or "").lower()
        if nombre.startswith("ollama"):
            procesos.append(proceso)
    return procesos


class Muestreador:
    """Hilo ligero que registra picos de memoria y uso de CPU/GPU."""

    def __init__(self, intervalo: float = 0.5) -> None:
        self.intervalo = intervalo
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None
        self.picos: dict = {}
        self._cpu: list[float] = []
        self._gpu_util: list[float] = []
        self._muestras = 0
        self._nvidia = bool(shutil.which("nvidia-smi"))

    def _rss_arbol(self, proceso) -> float:
        total = 0
        try:
            total += proceso.memory_info().rss
            for hijo in proceso.children(recursive=True):
                try:
                    total += hijo.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return total / _MB

    def _pico(self, clave: str, valor: float | None) -> None:
        if valor is None:
            return
        self.picos[clave] = round(max(self.picos.get(clave, 0.0), valor), 1)

    def _muestra(self, propio, ollama) -> None:
        self._pico("rss_pytest_y_mcp_mb", self._rss_arbol(propio))
        rss_ollama = 0.0
        for proceso in ollama:
            try:
                rss_ollama += proceso.memory_info().rss / _MB
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if ollama:
            self._pico("rss_ollama_mb", rss_ollama)
        memoria = psutil.virtual_memory()
        self._pico("ram_sistema_usada_pct", memoria.percent)
        self._pico("swap_usado_mb", psutil.swap_memory().used / _MB)
        self._cpu.append(psutil.cpu_percent(interval=None))
        if self._nvidia and self._muestras % 4 == 0:  # nvidia-smi cada ~2 s
            salida = _comando([
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ], timeout=3)
            usados, utilizacion = 0.0, 0.0
            for linea in salida.splitlines():
                partes = [p.strip() for p in linea.split(",")]
                try:
                    usados += float(partes[0])
                    utilizacion = max(utilizacion, float(partes[1]))
                except (IndexError, ValueError):
                    continue
            if salida:
                self._pico("vram_usada_mb", usados)
                self._gpu_util.append(utilizacion)
        self._muestras += 1

    def _ciclo(self) -> None:
        propio = psutil.Process(os.getpid())
        ollama = _procesos_ollama()
        psutil.cpu_percent(interval=None)  # la primera lectura siempre es 0
        while not self._detener.wait(self.intervalo):
            try:
                self._muestra(propio, ollama)
            except Exception:  # noqa: BLE001 - el muestreo nunca rompe el caso
                continue

    def iniciar(self) -> None:
        if psutil is None:
            return
        self._hilo = threading.Thread(target=self._ciclo, name="muestreo-rendimiento", daemon=True)
        self._hilo.start()

    def detener(self) -> dict:
        if self._hilo is not None:
            self._detener.set()
            self._hilo.join(timeout=5)
        resultado = dict(self.picos)
        if self._cpu:
            resultado["cpu_sistema_pct_media"] = round(statistics.fmean(self._cpu), 1)
            resultado["cpu_sistema_pct_max"] = round(max(self._cpu), 1)
        if self._gpu_util:
            resultado["gpu_util_pct_media"] = round(statistics.fmean(self._gpu_util), 1)
            resultado["gpu_util_pct_max"] = round(max(self._gpu_util), 1)
        resultado["muestras"] = self._muestras
        return resultado


# ------------------------------------------------------------ llamadas LLM


def instalar_registro_llm() -> None:
    """Callback en este proceso y hook para los subprocesos MCP."""
    os.environ["DEEPEVAL_PID_PRINCIPAL"] = str(os.getpid())
    if str(CARPETA_HOOK) not in sys.path:
        sys.path.insert(0, str(CARPETA_HOOK))
    rutas = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if str(CARPETA_HOOK) not in rutas:
        os.environ["PYTHONPATH"] = os.pathsep.join([str(CARPETA_HOOK), *rutas])
    import registro_llm  # noqa: PLC0415 - vive en _subproceso/

    registro_llm.registrar()


def leer_llamadas(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    llamadas = []
    for linea in ruta.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            llamadas.append(json.loads(linea))
        except ValueError:
            continue
    return llamadas


def resumir_llamadas(llamadas: list[dict]) -> dict:
    validas = [ll for ll in llamadas if not ll.get("error")]
    por_modelo: dict[str, dict] = {}
    for llamada in validas:
        modelo = llamada.get("modelo") or "desconocido"
        grupo = por_modelo.setdefault(modelo, {
            "llamadas": 0, "tokens_entrada": 0, "tokens_salida": 0,
            "s_generacion": 0.0, "s_prompt": 0.0, "s_carga": 0.0, "s_total": 0.0,
            "en_subproceso_mcp": 0,
        })
        grupo["llamadas"] += 1
        grupo["tokens_entrada"] += int(llamada.get("tokens_entrada") or 0)
        grupo["tokens_salida"] += int(llamada.get("tokens_salida") or 0)
        for clave in ("s_generacion", "s_prompt", "s_carga"):
            grupo[clave] += float(llamada.get(clave) or 0)
        grupo["s_total"] += float(llamada.get("s_total") or llamada.get("s_pared") or 0)
        if llamada.get("proceso") == "subproceso_mcp":
            grupo["en_subproceso_mcp"] += 1
    for grupo in por_modelo.values():
        grupo["tokens_por_s"] = (
            round(grupo["tokens_salida"] / grupo["s_generacion"], 2)
            if grupo["s_generacion"] > 0 else None
        )
        grupo["tokens_prompt_por_s"] = (
            round(grupo["tokens_entrada"] / grupo["s_prompt"], 2)
            if grupo["s_prompt"] > 0 else None
        )
        for clave in ("s_generacion", "s_prompt", "s_carga", "s_total"):
            grupo[clave] = round(grupo[clave], 3)
    return {
        "llamadas": len(validas),
        "errores": len(llamadas) - len(validas),
        "tokens_entrada": sum(g["tokens_entrada"] for g in por_modelo.values()),
        "tokens_salida": sum(g["tokens_salida"] for g in por_modelo.values()),
        "s_carga_modelos": round(sum(g["s_carga"] for g in por_modelo.values()), 3),
        "por_modelo": por_modelo,
    }


# ---------------------------------------------------------------- por caso


@dataclass
class Medicion:
    """Lo que se mide mientras corre un caso."""

    caso_id: str
    inicio: float = field(default_factory=time.perf_counter)
    inicio_reloj: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    turnos: list[dict] = field(default_factory=list)
    datos: dict = field(default_factory=dict)

    def turno(self, tipo: str, segundos: float, respuesta: dict | None = None) -> None:
        registro = {"tipo": tipo, "s": round(segundos, 3)}
        if respuesta:
            if respuesta.get("duracion") is not None:
                registro["s_servicio"] = respuesta.get("duracion")
            pasos = [
                {
                    "agente": paso.get("agente"),
                    "accion": paso.get("accion"),
                    "s": paso.get("duracion"),
                    "estado": paso.get("estado"),
                }
                for paso in respuesta.get("pasos") or []
                if isinstance(paso, dict)
            ]
            if pasos:
                registro["pasos"] = pasos
            registro["herramientas"] = len(respuesta.get("resultados") or []) or len(
                respuesta.get("herramientas") or []
            )
        self.turnos.append(registro)


@contextmanager
def medir(caso_id: str):
    """Mide un caso completo: tiempos, CPU, memoria y llamadas LLM."""
    medicion = Medicion(caso_id)
    if not activo():
        yield medicion
        return
    archivo_llm = Path(tempfile.gettempdir()) / f"deepeval_llm_{os.getpid()}_{time.time_ns()}.jsonl"
    anterior_llm = os.environ.get(VARIABLE_LLM)
    os.environ[VARIABLE_LLM] = str(archivo_llm)
    tiempos = os.times()
    muestreador = Muestreador()
    muestreador.iniciar()
    try:
        yield medicion
    finally:
        total = time.perf_counter() - medicion.inicio
        recursos = muestreador.detener()
        fin = os.times()
        if anterior_llm is None:
            os.environ.pop(VARIABLE_LLM, None)
        else:
            os.environ[VARIABLE_LLM] = anterior_llm
        cpu = {
            "s_cpu_pytest": round((fin.user - tiempos.user) + (fin.system - tiempos.system), 3),
        }
        if os.name != "nt":  # Windows no reporta CPU de hijos en os.times
            cpu["s_cpu_subprocesos"] = round(
                (fin.children_user - tiempos.children_user)
                + (fin.children_system - tiempos.children_system), 3
            )
        try:
            import resource  # noqa: PLC0415 - no existe en Windows

            # ru_maxrss: KB en Linux, bytes en macOS.
            divisor = _MB if platform.system() == "Darwin" else 1024
            recursos["rss_max_proceso_acumulado_mb"] = round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor, 1
            )
        except (ImportError, ValueError):
            pass
        llamadas = leer_llamadas(archivo_llm)
        try:
            archivo_llm.unlink(missing_ok=True)
        except OSError:
            pass
        final = next((t for t in reversed(medicion.turnos) if t["tipo"] == "final"), {})
        medicion.datos = {
            "inicio": medicion.inicio_reloj,
            "s_total": round(total, 3),
            "s_agente": final.get("s"),
            "s_servicio": final.get("s_servicio"),
            "s_turnos_previos": round(
                sum(t["s"] for t in medicion.turnos if t["tipo"] != "final"), 3
            ),
            "turnos": medicion.turnos,
            "cpu": cpu,
            "recursos": recursos,
            "llm": resumir_llamadas(llamadas),
            "ollama_al_final": ollama_modelos_cargados(),
        }


# ------------------------------------------------------------------ corrida


@dataclass
class RegistroCorrida:
    """Acumula lo medido en toda la sesión de pytest."""

    inicio: datetime = field(default_factory=datetime.now)
    equipo: dict | None = None
    casos: dict[str, dict] = field(default_factory=dict)

    def equipo_actual(self) -> dict:
        if self.equipo is None:
            self.equipo = dispositivo() if activo() else {}
        return self.equipo

    def caso(self, caso_id: str) -> dict:
        return self.casos.setdefault(caso_id, {"id": caso_id})

    def escribir(self, carpeta: Path, cobertura: dict) -> tuple[Path, Path] | None:
        ejecutados = [c for c in self.casos.values() if c.get("rendimiento")]
        if not ejecutados:
            return None
        carpeta.mkdir(parents=True, exist_ok=True)
        sello = self.inicio.strftime("%Y%m%d_%H%M%S")
        datos = {
            "inicio": self.inicio.isoformat(timespec="seconds"),
            "fin": datetime.now().isoformat(timespec="seconds"),
            "equipo": self.equipo_actual(),
            "resumen": self._resumen(ejecutados),
            "cobertura": cobertura,
            "casos": ejecutados,
        }
        ruta_json = carpeta / f"rendimiento_{sello}.json"
        ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        ruta_md = carpeta / f"rendimiento_{sello}.md"
        ruta_md.write_text(_markdown(datos), encoding="utf-8")
        return ruta_json, ruta_md

    @staticmethod
    def _resumen(casos: list[dict]) -> dict:
        def valores(clave: str, filtro=lambda c: True) -> list[float]:
            return [
                c["rendimiento"].get(clave)
                for c in casos
                if filtro(c) and c["rendimiento"].get(clave) is not None
            ]

        por_historia: dict[str, dict] = {}
        for historia in sorted({h for c in casos for h in c.get("historias") or []}):
            por_historia[historia] = resumen_numerico(
                valores("s_agente", lambda c, h=historia: h in (c.get("historias") or []))
            )
        modelos: dict[str, dict] = {}
        for caso in casos:
            for modelo, grupo in (caso["rendimiento"].get("llm") or {}).get("por_modelo", {}).items():
                acumulado = modelos.setdefault(modelo, {
                    "llamadas": 0, "tokens_entrada": 0, "tokens_salida": 0,
                    "s_generacion": 0.0, "s_prompt": 0.0, "s_carga": 0.0,
                })
                for clave in acumulado:
                    acumulado[clave] += grupo.get(clave) or 0
        for grupo in modelos.values():
            grupo["tokens_por_s"] = (
                round(grupo["tokens_salida"] / grupo["s_generacion"], 2) if grupo["s_generacion"] else None
            )
            for clave in ("s_generacion", "s_prompt", "s_carga"):
                grupo[clave] = round(grupo[clave], 3)
        picos: dict[str, float] = {}
        for caso in casos:
            for clave, valor in (caso["rendimiento"].get("recursos") or {}).items():
                if clave.endswith(("_mb", "_pct", "_pct_max")) and isinstance(valor, (int, float)):
                    picos[clave] = max(picos.get(clave, 0), valor)
        resultados: dict[str, int] = {}
        for caso in casos:
            resultados[caso.get("resultado") or "desconocido"] = resultados.get(caso.get("resultado") or "desconocido", 0) + 1
        return {
            "casos": len(casos),
            "resultados": resultados,
            "s_agente": resumen_numerico(valores("s_agente")),
            "s_total_con_turnos_previos": resumen_numerico(valores("s_total")),
            "s_evaluacion_juez": resumen_numerico(
                [c.get("s_evaluacion") for c in casos if c.get("s_evaluacion") is not None]
            ),
            "por_historia": por_historia,
            "por_modelo": modelos,
            "picos_recursos": {k: round(v, 1) for k, v in picos.items()},
        }


def _fmt(valor, sufijo: str = "") -> str:
    if valor is None or valor == "":
        return "–"
    if isinstance(valor, float):
        return f"{valor:,.2f}{sufijo}"
    return f"{valor}{sufijo}"


def _markdown(datos: dict) -> str:
    equipo, resumen = datos["equipo"], datos["resumen"]
    lineas = [
        f"# Rendimiento de la suite — {datos['inicio']}",
        "",
        "## Equipo",
        "",
        "| Campo | Valor |",
        "|---|---|",
    ]
    if equipo:
        gpus = "; ".join(
            f"{g.get('nombre')}" + (f" ({g['vram_mb']:.0f} MB)" if g.get("vram_mb") else "")
            for g in equipo.get("gpus") or []
        ) or "no detectada"
        energia = equipo.get("energia") or {}
        filas = [
            ("Equipo (id anónimo)", equipo.get("equipo_id")),
            ("Sistema", f"{equipo['sistema']['so']} {equipo['sistema']['version']} ({equipo['sistema']['arquitectura']})"),
            ("CPU", f"{equipo['cpu']['modelo']} · {equipo['cpu'].get('nucleos_fisicos') or '?'} físicos / {equipo['cpu'].get('nucleos_logicos')} lógicos"),
            ("RAM", _fmt(equipo.get("ram_gb"), " GB")),
            ("GPU", gpus),
            ("Energía", (
                "batería " + ("(conectado)" if energia.get("conectado") else "(desconectado)")
                if energia.get("bateria") else ("corriente" if energia else "–")
            )),
            ("Python", equipo.get("python")),
            ("Ollama", f"{equipo['ollama'].get('version') or 'no disponible'}"),
            ("Juez", equipo.get("juez") + (" (solo reglas)" if equipo.get("solo_reglas") else "")),
            ("Commit", (equipo.get("git") or {}).get("commit", "–")
             + (" (con cambios sin commit)" if (equipo.get("git") or {}).get("cambios_sin_commit") else "")),
        ]
        lineas += [f"| {k} | {_fmt(v)} |" for k, v in filas]
        modelos = equipo["ollama"].get("modelos_configurados") or {}
        if modelos:
            lineas += ["", "Modelos configurados: " + ", ".join(f"`{k}`={v}" for k, v in modelos.items())]
    s = resumen["s_agente"]
    lineas += [
        "",
        "## Resumen",
        "",
        f"- Casos medidos: **{resumen['casos']}** · resultados: "
        + ", ".join(f"{k}={v}" for k, v in sorted(resumen["resultados"].items())),
        f"- Tiempo del agente (turno evaluado): mediana **{_fmt(s.get('p50'), ' s')}**, "
        f"p95 **{_fmt(s.get('p95'), ' s')}**, máx {_fmt(s.get('max'), ' s')}, total {_fmt(s.get('total'), ' s')}",
        f"- Tiempo del juez: total {_fmt(resumen['s_evaluacion_juez'].get('total'), ' s')}, "
        f"mediana {_fmt(resumen['s_evaluacion_juez'].get('p50'), ' s')}",
    ]
    if resumen["picos_recursos"]:
        lineas.append("- Picos: " + ", ".join(f"{k}={_fmt(v)}" for k, v in resumen["picos_recursos"].items()))
    if resumen["por_modelo"]:
        lineas += ["", "## Modelos (llamadas vía LangChain/Ollama)", "",
                   "| Modelo | Llamadas | Tokens entrada | Tokens salida | Tokens/s | Carga (s) |",
                   "|---|---:|---:|---:|---:|---:|"]
        for modelo, g in sorted(resumen["por_modelo"].items()):
            lineas.append(
                f"| {modelo} | {g['llamadas']} | {g['tokens_entrada']:,} | {g['tokens_salida']:,} "
                f"| {_fmt(g.get('tokens_por_s'))} | {_fmt(g.get('s_carga'))} |"
            )
    if resumen["por_historia"]:
        lineas += ["", "## Tiempo del agente por historia", "",
                   "| Historia | Casos | Mediana (s) | p95 (s) | Máx (s) |", "|---|---:|---:|---:|---:|"]
        for historia, r in resumen["por_historia"].items():
            lineas.append(f"| {historia} | {r.get('n', 0)} | {_fmt(r.get('p50'))} | {_fmt(r.get('p95'))} | {_fmt(r.get('max'))} |")
    cobertura = datos.get("cobertura") or {}
    if cobertura:
        lineas += ["", "## Cobertura de criterios de aceptación", "",
                   "| Criterio | Casos | Resultado |", "|---|---|---|"]
        for criterio, info in cobertura.items():
            lineas.append(
                f"| {criterio} | {', '.join(info['casos']) or '—'} | {info['estado']} |"
            )
    lineas += ["", "## Casos", "",
               "| Caso | Historias | Resultado | Agente (s) | Total (s) | Juez (s) | Llamadas LLM | Tokens/s | Pico RAM pytest+MCP (MB) |",
               "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for caso in sorted(datos["casos"], key=lambda c: c["id"]):
        r = caso["rendimiento"]
        llm = r.get("llm") or {}
        tps = [g.get("tokens_por_s") for g in (llm.get("por_modelo") or {}).values() if g.get("tokens_por_s")]
        lineas.append(
            f"| `{caso['id']}` | {', '.join(caso.get('historias') or [])} | {caso.get('resultado', '–')} "
            f"| {_fmt(r.get('s_agente'))} | {_fmt(r.get('s_total'))} | {_fmt(caso.get('s_evaluacion'))} "
            f"| {llm.get('llamadas', 0)} | {_fmt(round(statistics.fmean(tps), 2) if tps else None)} "
            f"| {_fmt((r.get('recursos') or {}).get('rss_pytest_y_mcp_mb'))} |"
        )
    lineas += ["", "_Los tokens solo cuentan llamadas hechas con LangChain (orquestador y subprocesos MCP)._", ""]
    return "\n".join(lineas)


__all__ = [
    "CORRIDA",
    "Medicion",
    "RegistroCorrida",
    "activo",
    "dispositivo",
    "instalar_registro_llm",
    "medir",
    "resumen_numerico",
]


CORRIDA = RegistroCorrida()
