
import sqlite3
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SCRIPT = AQUI / "crear_bd.sql"
DESTINO = AQUI / "banamex.db"

DESTINO.unlink(missing_ok=True)  # el script recrea todo desde cero
conexion = sqlite3.connect(DESTINO)
conexion.executescript(SCRIPT.read_text(encoding="utf-8"))
conexion.close()
print(f"Base creada: {DESTINO}")
