"""Archivos adjuntos sintéticos para los casos de US-06, US-07 y US-08.

Se generan al vuelo (no se versionan binarios) con datos conocidos, para que
los casos puedan exigir cifras exactas. Un caso los pide como
``adjunto: fixture:<nombre>``; cualquier otro valor se toma como ruta local.

Igual que la UI, el archivo se copia a la carpeta de cargas de la identidad
autenticada (``subject_upload_root``): con sesión, Document Intelligence
rechaza rutas fuera de esa carpeta.
"""

from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
CARPETA_GENERADOS = AQUI / "fixtures_generados"

# Datos que los casos verifican; cambiar aquí obliga a revisar los YAML.
COMPROBANTE = {
    "banco": "Banamex",
    "referencia": "REF-458812",
    "fecha": "15/06/2026",
    "concepto": "Pago de tarjeta de credito",
    "importe": "$1,234.56",
}
ESTADO_CUENTA = {
    "titular": "Cliente de prueba sintetico",
    "cuenta": "100000",
    "fecha_corte": "30 de junio de 2026",
    "saldo_inicial": "$10,227.26",
    "depositos": "$3,850.00",
    "retiros": "$3,747.73",
    "saldo_final": "$10,329.53",
}


def _fuente(tamano: int):
    from PIL import ImageFont

    for candidata in (
        "DejaVuSans.ttf",
        "Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidata, tamano)
        except OSError:
            continue
    return ImageFont.load_default()


def _comprobante_png(destino: Path) -> None:
    from PIL import Image, ImageDraw

    imagen = Image.new("RGB", (900, 560), "white")
    dibujo = ImageDraw.Draw(imagen)
    grande, normal = _fuente(40), _fuente(30)
    dibujo.text((40, 30), "COMPROBANTE DE PAGO", fill="black", font=grande)
    lineas = [
        f"Banco: {COMPROBANTE['banco']}",
        f"Referencia: {COMPROBANTE['referencia']}",
        f"Fecha: {COMPROBANTE['fecha']}",
        f"Concepto: {COMPROBANTE['concepto']}",
        f"Importe pagado: {COMPROBANTE['importe']} MXN",
        "Operacion exitosa",
    ]
    for i, linea in enumerate(lineas):
        dibujo.text((40, 120 + i * 62), linea, fill="black", font=normal)
    imagen.save(destino, "PNG")


def _estado_cuenta_pdf_escaneado(destino: Path) -> None:
    """PDF de una sola imagen: sin capa de texto, como un escaneo."""
    from PIL import Image, ImageDraw

    imagen = Image.new("RGB", (1240, 1754), "white")
    dibujo = ImageDraw.Draw(imagen)
    fuente = _fuente(36)
    dibujo.text((80, 80), "ESTADO DE CUENTA (ESCANEADO)", fill="black", font=_fuente(48))
    for i, (clave, valor) in enumerate(ESTADO_CUENTA.items()):
        dibujo.text((80, 220 + i * 70), f"{clave.replace('_', ' ')}: {valor}",
                    fill="black", font=fuente)
    imagen.save(destino, "PDF", resolution=150)


def _docx(destino: Path) -> None:
    """DOCX mínimo válido sin depender de python-docx."""
    parrafos = [
        "Estado de cuenta de cheques",
        f"Titular: {ESTADO_CUENTA['titular']}",
        f"Cuenta: {ESTADO_CUENTA['cuenta']}",
        f"Fecha de corte: {ESTADO_CUENTA['fecha_corte']}",
        f"Saldo inicial: {ESTADO_CUENTA['saldo_inicial']}",
        f"Depositos del periodo: {ESTADO_CUENTA['depositos']}",
        f"Retiros del periodo: {ESTADO_CUENTA['retiros']}",
        f"Saldo final: {ESTADO_CUENTA['saldo_final']}",
        "Comisiones del periodo: $0.00",
        "Documento sintetico para pruebas; no corresponde a un cliente real.",
    ]
    cuerpo = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in parrafos
    )
    documento = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{cuerpo}</w:body></w:document>"
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relaciones = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zz:
        zz.writestr("[Content_Types].xml", tipos)
        zz.writestr("_rels/.rels", relaciones)
        zz.writestr("word/document.xml", documento)


def _csv(destino: Path) -> None:
    destino.write_text(
        "fecha,concepto,tipo,importe\n"
        "2026-06-03,Deposito nomina,deposito,2500.00\n"
        "2026-06-10,Pago servicio luz,retiro,845.20\n"
        "2026-06-18,Transferencia recibida,deposito,1350.00\n"
        "2026-06-21,Pago tarjeta de credito,retiro,2902.53\n",
        encoding="utf-8",
    )


def _bytes_aleatorios(destino: Path, megas: float) -> None:
    """Archivo que excede el límite; el servicio lo rechaza antes de leerlo."""
    with destino.open("wb") as salida:
        restante = int(megas * 1024 * 1024)
        bloque = os.urandom(1024 * 1024)
        while restante > 0:
            salida.write(bloque[: min(restante, len(bloque))])
            restante -= len(bloque)


def _imagen_formato_no_soportado(destino: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 64), "white").save(destino, "TIFF")


GENERADORES = {
    "comprobante.png": _comprobante_png,
    "comprobante.tiff": _imagen_formato_no_soportado,
    "imagen_grande.png": lambda d: _bytes_aleatorios(d, 10.5),
    "estado_cuenta.docx": _docx,
    "movimientos_junio.csv": _csv,
    "estado_cuenta_escaneado.pdf": _estado_cuenta_pdf_escaneado,
    "documento_grande.pdf": lambda d: _bytes_aleatorios(d, 25.5),
    "programa.exe": lambda d: d.write_bytes(b"MZ" + os.urandom(2048)),
}


def generar(nombre: str) -> Path:
    if nombre not in GENERADORES:
        raise KeyError(f"fixture desconocido {nombre!r}; disponibles: {sorted(GENERADORES)}")
    CARPETA_GENERADOS.mkdir(parents=True, exist_ok=True)
    destino = CARPETA_GENERADOS / nombre
    if not destino.exists():
        temporal = destino.with_name(destino.name + ".tmp")
        GENERADORES[nombre](temporal)
        temporal.replace(destino)
    return destino


def preparar_adjunto(adjunto: str) -> tuple[Path, str]:
    """Devuelve (ruta lista para el servicio, nombre visible)."""
    if adjunto.startswith("fixture:"):
        origen = generar(adjunto.split(":", 1)[1])
    else:
        origen = Path(adjunto).expanduser()
        if not origen.is_absolute():
            origen = AQUI.parent / origen
    nombre = origen.name

    token = os.environ.get("PROYECTO_AUTH_TOKEN", "").strip()
    if not token:
        return origen.resolve(), nombre  # sin sesión el servicio no restringe rutas

    from proyecto.security.file_access import new_upload_dir
    from proyecto.security.local_auth import authenticated, principal

    with authenticated(token):
        subject = str(principal()["subject"])
    destino = new_upload_dir(subject) / f"adjunto_{uuid.uuid4().hex[:8]}{origen.suffix}"
    shutil.copyfile(origen, destino)
    return destino, nombre


__all__ = ["COMPROBANTE", "ESTADO_CUENTA", "generar", "preparar_adjunto"]
