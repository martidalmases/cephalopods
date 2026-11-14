"""
Almacenamiento en Zarr (lectura con xarray), compartido por el recolector, las especies y la API.

    DATOS_DIR/
      ambiente.zarr       Un array por variable (time, lat, lon) con la MEDIA MENSUAL en una
                          rejilla MUNDIAL de PASO grados (1° por defecto: 180 x 360 celdas),
                          más la máscara de mar `mar` (lat, lon). El eje de tiempo es fijo, un
                          paso por mes de ORIGEN a FIN (fecha = día 1 de cada mes).
      anual.zarr          Media anual por celda (especies.py).
      especies/*.zarr     Un almacén por especie, con resultados por año (especies.py).

Los trozos en disco son de 12 meses x TROZO_ESPACIO x TROZO_ESPACIO celdas: leer la serie de
un punto o el mapa de un mes sólo toca unos pocos trozos, nunca el planeta entero, y los
trozos sin datos (años antes de un satélite) no se guardan.

El recolector es el único que escribe en ambiente.zarr (con la API de zarr, por bloques
de meses); el resto lo lee con xarray.
"""

from __future__ import annotations

import os
import shutil
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import xarray as xr
import zarr

# Los metadatos consolidados no son parte de la especificación v3, pero zarr-python y
# xarray los usan para abrir el almacén con una sola lectura.
warnings.filterwarnings("ignore", message="Consolidated metadata is currently not part")

DATOS_DIR = Path(os.environ.get("DATOS_DIR", Path(__file__).parent / "datos"))
RUTA_AMBIENTE = DATOS_DIR / "ambiente.zarr"
RUTA_ANUAL = DATOS_DIR / "anual.zarr"
DIR_ESPECIES = DATOS_DIR / "especies"

# Resolución de la rejilla mundial. 1° cabe holgado en disco y RAM pequeños; 0,5° multiplica
# por 4 el espacio (cambiarla obliga a volver a cargar los datos).
PASO = float(os.environ.get("DATOS_PASO", "1.0"))
LATS = np.round(np.arange(-90 + PASO / 2, 90, PASO), 4)      # centros de celda, de sur a norte
LONS = np.round(np.arange(-180 + PASO / 2, 180, PASO), 4)    # centros de celda, de oeste a este
NLAT, NLON = len(LATS), len(LONS)
ORIGEN = date(1980, 1, 1)            # primer mes de MERRA-2
FIN = date(2040, 12, 1)
NT = (FIN.year - ORIGEN.year) * 12 + FIN.month - ORIGEN.month + 1   # 732 meses
TROZO_TIEMPO = 12                    # meses por trozo en disco
TROZO_ESPACIO = 90                   # celdas por lado de cada trozo en disco


def indice_mes(d: date) -> int:
    """Índice del mes de `d` en el eje de tiempo (el día se ignora)."""
    return (d.year - ORIGEN.year) * 12 + d.month - ORIGEN.month


def mes_indice(i: int) -> date:
    """Día 1 del mes con índice `i`."""
    a, m = divmod(ORIGEN.month - 1 + int(i), 12)
    return date(ORIGEN.year + a, m + 1, 1)


def sumar_meses(d: date, n: int) -> date:
    return mes_indice(indice_mes(d) + n)


def indices_celda(lat, lon) -> tuple[np.ndarray, np.ndarray]:
    """Fila y columna de la celda que contiene cada punto. Admite longitudes en -180..180 o 0..360."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    i = np.clip(np.floor((lat + 90) / PASO).astype(int), 0, NLAT - 1)
    j = np.floor(((lon + 180) % 360) / PASO).astype(int) % NLON
    return i, j


def a_rejilla(valores: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """
    Media por celda de una rejilla de origen cualquiera: `valores` (len(lats), len(lons)),
    con NaN donde no hay dato. Devuelve (NLAT, NLON) float32, NaN en celdas sin ningún punto.
    """
    i = indices_celda(lats, 0)[0]
    j = indices_celda(0, lons)[1]
    ii, jj = np.meshgrid(i, j, indexing="ij")
    ok = ~np.isnan(valores)
    suma = np.zeros((NLAT, NLON))
    cuenta = np.zeros((NLAT, NLON))
    np.add.at(suma, (ii[ok], jj[ok]), valores[ok])
    np.add.at(cuenta, (ii[ok], jj[ok]), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cuenta > 0, suma / cuenta, np.nan).astype("float32")


# --------------------------------------------------------------------------- escritura

def abrir_escritura(variables: list[str], unidades: dict[str, str]) -> zarr.Group:
    """Abre ambiente.zarr para escribir, creando el almacén y las variables que falten."""
    DATOS_DIR.mkdir(parents=True, exist_ok=True)
    # Sin metadatos consolidados: al escribir hay que ver las variables recién creadas.
    g = zarr.open_group(RUTA_AMBIENTE, mode="a", use_consolidated=False)
    if "time" not in g:
        dias = np.array([(mes_indice(i) - ORIGEN).days for i in range(NT)], dtype="int32")
        g.create_array("time", data=dias, dimension_names=["time"],
                       attributes={"units": f"days since {ORIGEN.isoformat()}", "calendar": "proleptic_gregorian"})
        g.create_array("lat", data=LATS, dimension_names=["lat"], attributes={"units": "degrees_north"})
        g.create_array("lon", data=LONS, dimension_names=["lon"], attributes={"units": "degrees_east"})
        g.create_array("mar", data=np.zeros((NLAT, NLON), dtype=bool), dimension_names=["lat", "lon"],
                       attributes={"descripcion": "celda de mar: fracción de océano de MERRA-2 >= 0,5 o algún dato de satélite",
                                   "paso_grados": PASO})
    for v in variables:
        if v not in g:
            g.create_array(v, shape=(NT, NLAT, NLON), dtype="float32", fill_value=np.nan,
                           chunks=(TROZO_TIEMPO, min(TROZO_ESPACIO, NLAT), min(TROZO_ESPACIO, NLON)),
                           dimension_names=["time", "lat", "lon"],
                           attributes={"units": unidades.get(v, ""), "desde": None, "hasta": None})
    return g


def escribir_bloque(g: zarr.Group, variable: str, inicio: date, bloque: np.ndarray) -> int:
    """
    Escribe `bloque` (meses, lat, lon) a partir del mes de `inicio`. Donde el bloque es
    NaN se conserva lo que hubiera. Actualiza los meses desde/hasta ("AAAA-MM") de la
    variable. Devuelve el número de valores nuevos válidos.
    """
    t0 = indice_mes(inicio)
    arr = g[variable]
    actual = arr[t0:t0 + len(bloque)]
    arr[t0:t0 + len(bloque)] = np.where(np.isnan(bloque), actual, bloque).astype("float32")

    meses_validos = np.flatnonzero(~np.isnan(bloque).all(axis=(1, 2)))
    if len(meses_validos):
        primero = mes_indice(t0 + meses_validos[0]).isoformat()[:7]
        ultimo = mes_indice(t0 + meses_validos[-1]).isoformat()[:7]
        desde, hasta = arr.attrs.get("desde"), arr.attrs.get("hasta")
        arr.attrs.update({"desde": min(filter(None, [desde, primero])),
                          "hasta": max(filter(None, [hasta, ultimo]))})
    return int((~np.isnan(bloque)).sum())


def marcar_mar(g: zarr.Group, celdas: np.ndarray) -> None:
    g["mar"][:] = g["mar"][:] | celdas


def consolidar() -> None:
    """Actualiza los metadatos consolidados para que los lectores vean los cambios."""
    zarr.consolidate_metadata(RUTA_AMBIENTE)


def guardar_dataset(ds: xr.Dataset, ruta: Path, trozos: dict | None = None) -> None:
    """Escribe un Dataset completo sustituyendo el anterior casi atómicamente."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp, viejo = ruta.with_suffix(".tmp.zarr"), ruta.with_suffix(".old.zarr")
    shutil.rmtree(tmp, ignore_errors=True)
    codificacion = {}
    if trozos:
        for v in ds.data_vars:
            codificacion[v] = {"chunks": tuple(min(trozos.get(d, n), n) for d, n in zip(ds[v].dims, ds[v].shape))}
    ds.to_zarr(tmp, mode="w", consolidated=True, encoding=codificacion)
    if ruta.exists():
        os.replace(ruta, viejo)
    os.replace(tmp, ruta)
    shutil.rmtree(viejo, ignore_errors=True)


# --------------------------------------------------------------------------- lectura

def medias_anuales(g: zarr.Group, variable: str, anios: np.ndarray, meses_minimos: int = 9,
                   franja: int = 45) -> np.ndarray:
    """
    Media anual (años, lat, lon) de una variable de ambiente.zarr, sólo donde hay al menos
    `meses_minimos` meses con dato. Se lee por franjas de latitud: pocas decenas de MB de RAM.
    """
    t0 = indice_mes(date(int(anios[0]), 1, 1))
    t1 = indice_mes(date(int(anios[-1]), 12, 1)) + 1
    anual = np.full((len(anios), NLAT, NLON), np.nan, dtype="float32")
    for a in range(0, NLAT, franja):
        b = min(a + franja, NLAT)
        m = g[variable][t0:t1, a:b, :].reshape(len(anios), 12, b - a, NLON)
        cuenta = (~np.isnan(m)).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            media = np.nansum(m, axis=1) / cuenta
        anual[:, a:b, :] = np.where(cuenta >= meses_minimos, media, np.nan)
    return anual


def abrir_ambiente() -> xr.Dataset | None:
    if not RUTA_AMBIENTE.exists():
        return None
    return xr.open_zarr(RUTA_AMBIENTE, consolidated=None)


def celda_mas_cercana(mar: np.ndarray, lat: float, lon: float, max_km: float | None = None) -> tuple[int, int] | None:
    """Celda de mar más cercana a (lat, lon), o None si está a más de `max_km` (por defecto, 1,5 celdas)."""
    ii, jj = np.nonzero(mar)
    if not len(ii):
        return None
    d = distancia_km(lat, lon, LATS[ii], LONS[jj])
    n = int(np.argmin(d))
    if d[n] > (max_km if max_km is not None else 1.5 * PASO * 111.2):
        return None
    return int(ii[n]), int(jj[n])


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia del círculo máximo; admite arrays de numpy."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))
