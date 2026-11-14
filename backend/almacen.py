"""
Almacenamiento en Zarr (lectura con xarray), compartido por el recolector, las especies y la API.

    DATOS_DIR/
      ambiente.zarr       Un array por variable (time, lat, lon) en la rejilla de 0,5° del
                          Mediterráneo, más la máscara de mar `mar` (lat, lon). El eje de
                          tiempo es diario y fijo (ORIGEN..FIN): los trozos sin datos no se
                          guardan en disco, así que años vacíos no ocupan sitio.
                          Las variables de Giovanni sólo tienen valor en las celdas de 1°.
      climatologia.zarr   Media por mes del año (especies.py).
      especies/*.zarr     Un almacén por especie (especies.py).

El recolector es el único que escribe en ambiente.zarr (con la API de zarr, por bloques
de días); el resto lo lee con xarray.
"""

from __future__ import annotations

import math
import os
import shutil
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import xarray as xr
import zarr

# Los metadatos consolidados no son parte de la especificación v3, pero zarr-python y
# xarray los usan para abrir el almacén con una sola lectura.
warnings.filterwarnings("ignore", message="Consolidated metadata is currently not part")

DATOS_DIR = Path(os.environ.get("DATOS_DIR", Path(__file__).parent / "datos"))
RUTA_AMBIENTE = DATOS_DIR / "ambiente.zarr"
RUTA_CLIMATOLOGIA = DATOS_DIR / "climatologia.zarr"
DIR_ESPECIES = DATOS_DIR / "especies"

PASO = 0.5
LATS = np.round(np.arange(30.0, 46.0 + PASO / 2, PASO), 2)    # 33 filas
LONS = np.round(np.arange(-6.0, 36.5 + PASO / 2, PASO), 2)    # 86 columnas
ORIGEN = date(2000, 1, 1)
FIN = date(2040, 12, 31)
NT = (FIN - ORIGEN).days + 1
TROZO_TIEMPO = 32  # días por trozo en disco


def _mascara_mediterraneo() -> np.ndarray:
    """Recorte aproximado: sin Atlántico, golfo de Vizcaya ni mar Negro/Mármara."""
    la, lo = np.meshgrid(LATS, LONS, indexing="ij")
    return ~((lo < -5.5) | ((la > 43.0) & (lo < 0.0)) | ((la > 40.0) & (lo > 26.5)))


MEDITERRANEO = _mascara_mediterraneo()
# Celdas de lat/lon enteras: la rejilla de 1° que se consulta en Giovanni (un punto por petición).
CELDAS_1_GRADO = np.outer(LATS == np.round(LATS), LONS == np.round(LONS))


def indice_fecha(d: date) -> int:
    return (d - ORIGEN).days


def fecha_indice(i: int) -> date:
    return ORIGEN + timedelta(days=int(i))


def indice_celda(lat: float, lon: float) -> tuple[int, int] | None:
    i, j = round((lat - LATS[0]) / PASO), round((lon - LONS[0]) / PASO)
    return (i, j) if 0 <= i < len(LATS) and 0 <= j < len(LONS) else None


# --------------------------------------------------------------------------- escritura

def abrir_escritura(variables: list[str], unidades: dict[str, str]) -> zarr.Group:
    """Abre ambiente.zarr para escribir, creando el almacén y las variables que falten."""
    DATOS_DIR.mkdir(parents=True, exist_ok=True)
    g = zarr.open_group(RUTA_AMBIENTE, mode="a")
    if "time" not in g:
        g.create_array("time", data=np.arange(NT, dtype="int32"), dimension_names=["time"],
                       attributes={"units": f"days since {ORIGEN.isoformat()}", "calendar": "proleptic_gregorian"})
        g.create_array("lat", data=LATS, dimension_names=["lat"], attributes={"units": "degrees_north"})
        g.create_array("lon", data=LONS, dimension_names=["lon"], attributes={"units": "degrees_east"})
        g.create_array("mar", data=np.zeros((len(LATS), len(LONS)), dtype=bool), dimension_names=["lat", "lon"],
                       attributes={"descripcion": "celda de mar con algún dato de satélite"})
    for v in variables:
        if v not in g:
            g.create_array(v, shape=(NT, len(LATS), len(LONS)), chunks=(TROZO_TIEMPO, len(LATS), len(LONS)),
                           dtype="float32", fill_value=np.nan, dimension_names=["time", "lat", "lon"],
                           attributes={"units": unidades.get(v, ""), "desde": None, "hasta": None})
    return g


def escribir_bloque(g: zarr.Group, variable: str, inicio: date, bloque: np.ndarray) -> int:
    """
    Escribe `bloque` (días, lat, lon) a partir de `inicio`. Donde el bloque es NaN se
    conserva lo que hubiera. Actualiza las fechas desde/hasta de la variable.
    Devuelve el número de valores nuevos válidos.
    """
    t0 = indice_fecha(inicio)
    arr = g[variable]
    actual = arr[t0:t0 + len(bloque)]
    arr[t0:t0 + len(bloque)] = np.where(np.isnan(bloque), actual, bloque).astype("float32")

    dias_validos = np.flatnonzero(~np.isnan(bloque).all(axis=(1, 2)))
    if len(dias_validos):
        primero, ultimo = fecha_indice(t0 + dias_validos[0]), fecha_indice(t0 + dias_validos[-1])
        desde, hasta = arr.attrs.get("desde"), arr.attrs.get("hasta")
        arr.attrs.update({"desde": min(filter(None, [desde, primero.isoformat()])),
                          "hasta": max(filter(None, [hasta, ultimo.isoformat()]))})
    return int((~np.isnan(bloque)).sum())


def marcar_mar(g: zarr.Group, celdas: np.ndarray) -> None:
    g["mar"][:] = g["mar"][:] | (celdas & MEDITERRANEO)


def consolidar() -> None:
    """Actualiza los metadatos consolidados para que los lectores vean los cambios."""
    zarr.consolidate_metadata(RUTA_AMBIENTE)


def guardar_dataset(ds: xr.Dataset, ruta: Path) -> None:
    """Escribe un Dataset completo sustituyendo el anterior casi atómicamente."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp, viejo = ruta.with_suffix(".tmp.zarr"), ruta.with_suffix(".old.zarr")
    shutil.rmtree(tmp, ignore_errors=True)
    ds.to_zarr(tmp, mode="w", consolidated=True)
    if ruta.exists():
        os.replace(ruta, viejo)
    os.replace(tmp, ruta)
    shutil.rmtree(viejo, ignore_errors=True)


# --------------------------------------------------------------------------- lectura

def abrir_ambiente() -> xr.Dataset | None:
    if not RUTA_AMBIENTE.exists():
        return None
    return xr.open_zarr(RUTA_AMBIENTE, consolidated=None)


def celda_mas_cercana(mar: np.ndarray, lat: float, lon: float, solo_1_grado: bool = False,
                      max_km: float = 150.0) -> tuple[int, int] | None:
    """Celda de mar más cercana a (lat, lon), o None si está a más de `max_km`."""
    mascara = mar & CELDAS_1_GRADO if solo_1_grado else mar
    ii, jj = np.nonzero(mascara)
    if not len(ii):
        return None
    k = math.cos(math.radians(lat))
    d2 = (LATS[ii] - lat) ** 2 + ((LONS[jj] - lon) * k) ** 2
    n = int(np.argmin(d2))
    if math.sqrt(d2[n]) * 111.2 > max_km:
        return None
    return int(ii[n]), int(jj[n])


def distancia_km(lat1: float, lon1: float, lat2, lon2):
    """Distancia del círculo máximo; admite arrays de numpy en lat2/lon2."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))
