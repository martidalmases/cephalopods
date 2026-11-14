"""
Base de datos SQLite compartida por el recolector y la API.

Tablas:
  punto(id, lat, lon, giovanni)   Puntos de mar de la rejilla de 0,5°.
                                  giovanni=1 marca el subconjunto de 1° que se
                                  consulta en Giovanni (una petición por punto).
  medicion(punto_id, variable, fecha, valor)   Valor diario (o del compuesto de 8 días).
"""

from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path

RUTA_BD = Path(os.environ.get("DATOS_BD", Path(__file__).parent / "mediterraneo.sqlite"))
PASO_REJILLA = 0.5  # grados

ESQUEMA = """
CREATE TABLE IF NOT EXISTS punto (
    id       INTEGER PRIMARY KEY,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    giovanni INTEGER NOT NULL DEFAULT 0,
    UNIQUE (lat, lon)
);
CREATE TABLE IF NOT EXISTS medicion (
    punto_id INTEGER NOT NULL REFERENCES punto(id),
    variable TEXT    NOT NULL,
    fecha    TEXT    NOT NULL,   -- AAAA-MM-DD
    valor    REAL    NOT NULL,
    PRIMARY KEY (punto_id, variable, fecha)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS medicion_variable_fecha ON medicion (variable, fecha);
"""


def conectar(ruta: Path | str = RUTA_BD) -> sqlite3.Connection:
    con = sqlite3.connect(ruta, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # la API puede leer mientras el recolector escribe
    con.executescript(ESQUEMA)
    return con


def ajustar_a_rejilla(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / PASO_REJILLA) * PASO_REJILLA, round(lon / PASO_REJILLA) * PASO_REJILLA)


def es_mediterraneo(lat: float, lon: float) -> bool:
    """Recorte aproximado: caja del Mediterráneo sin Atlántico, golfo de Vizcaya ni mar Negro/Mármara."""
    if not (30.0 <= lat <= 46.0 and -6.0 <= lon <= 36.5):
        return False
    if lon < -5.5:                    # Atlántico (oeste del Estrecho)
        return False
    if lat > 43.0 and lon < 0.0:      # golfo de Vizcaya
        return False
    if lat > 40.0 and lon > 26.5:     # mar de Mármara y mar Negro
        return False
    return True


def punto_mas_cercano(con: sqlite3.Connection, lat: float, lon: float,
                      solo_giovanni: bool = False) -> sqlite3.Row | None:
    """Punto de la rejilla más cercano (distancia aproximada con corrección por latitud)."""
    filtro = "AND giovanni = 1" if solo_giovanni else ""
    for radio in (1.0, 3.0):
        filas = con.execute(
            f"SELECT id, lat, lon FROM punto WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? {filtro}",
            (lat - radio, lat + radio, lon - radio, lon + radio)).fetchall()
        if filas:
            k = math.cos(math.radians(lat))
            return min(filas, key=lambda p: (p["lat"] - lat) ** 2 + ((p["lon"] - lon) * k) ** 2)
    return None


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))
