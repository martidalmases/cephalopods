"""Regiones de los mapas de la web (sólo numpy: lo usan exportar_trends.py y habitat.py)."""

from __future__ import annotations

import numpy as np


def mascara_mediterraneo(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Sin Atlántico, golfo de Vizcaya ni mar Negro/Mármara (sobre centros de celda)."""
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    return ~((lo < -5.5) | ((la > 43.0) & (lo < 0.0)) | ((la > 40.0) & (lo > 26.5)))


# Caja (bordes de celda) del Mediterráneo: la misma en todos los mapas de la web.
MEDITERRANEO = {"name": "Mediterranean Sea", "lat": (30, 46), "lon": (-6, 37), "mascara": mascara_mediterraneo}
