"""
Almacenamiento en Zarr (lectura con xarray), compartido por el recolector, las especies y la API.

Hay dos rejillas MUNDIALES (ver REJILLAS):
  * BASE, 1° (180 x 360 celdas): casi todas las variables (MERRA-2, SMAP, fluorescencia).
  * FINA, 0,5° (360 x 720 celdas): temperatura del mar (OISST) y clorofila. De la temperatura
    salen además, por año, la mínima de invierno, la máxima de verano y los meses de ola de
    calor marina (especies.py). Las especies se calculan en esta rejilla.

    DATOS_DIR/
      ambiente.zarr       rejilla BASE: un array por variable (time, lat, lon) con la MEDIA
                          MENSUAL, más la máscara de mar `mar` (lat, lon)
      ambiente_fino.zarr  lo mismo en la rejilla FINA
      anual.zarr          medias y extremos anuales en la rejilla FINA (especies.py)
      especies/*.zarr     un almacén por especie, con resultados por año (especies.py)
      trends/             ficheros para la pantalla Trends (exportar_trends.py)

El eje de tiempo es fijo, un paso por mes de ORIGEN a FIN (fecha = día 1 de cada mes). Los
trozos en disco son de 12 meses x TROZO_ESPACIO x TROZO_ESPACIO celdas: leer la serie de un
punto o el mapa de un mes sólo toca unos pocos trozos, nunca el planeta entero, y los trozos
sin datos (años antes de un satélite) no se guardan.

El recolector es el único que escribe en los almacenes de ambiente (con la API de zarr, por
bloques de meses); el resto los lee con xarray.
"""

from __future__ import annotations

import os
import shutil
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import xarray as xr
import zarr

# Los metadatos consolidados no son parte de la especificación v3, pero zarr-python y
# xarray los usan para abrir el almacén con una sola lectura.
warnings.filterwarnings("ignore", message="Consolidated metadata is currently not part")

DATOS_DIR = Path(os.environ.get("DATOS_DIR", Path(__file__).parent / "datos"))
RUTA_ANUAL = DATOS_DIR / "anual.zarr"
DIR_ESPECIES = DATOS_DIR / "especies"

ORIGEN = date(1980, 1, 1)            # primer mes de MERRA-2
FIN = date(2040, 12, 1)
NT = (FIN.year - ORIGEN.year) * 12 + FIN.month - ORIGEN.month + 1   # 732 meses
TROZO_TIEMPO = 12                    # meses por trozo en disco
TROZO_ESPACIO = 90                   # celdas por lado de cada trozo en disco


@dataclass(frozen=True)
class Rejilla:
    """Rejilla mundial regular: centros de celda de sur a norte y de oeste a este."""
    nombre: str
    paso: float
    ruta: Path                        # almacén de las medias mensuales
    lats: np.ndarray = field(init=False, repr=False, compare=False)
    lons: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "lats", np.round(np.arange(-90 + self.paso / 2, 90, self.paso), 4))
        object.__setattr__(self, "lons", np.round(np.arange(-180 + self.paso / 2, 180, self.paso), 4))

    @property
    def nlat(self) -> int:
        return len(self.lats)

    @property
    def nlon(self) -> int:
        return len(self.lons)


BASE = Rejilla("base", float(os.environ.get("DATOS_PASO", "1.0")), DATOS_DIR / "ambiente.zarr")
FINA = Rejilla("fina", float(os.environ.get("DATOS_PASO_FINO", "0.5")), DATOS_DIR / "ambiente_fino.zarr")
REJILLAS = (BASE, FINA)
FACTOR_FINO = int(round(BASE.paso / FINA.paso))   # celdas finas por lado en cada celda base (2)

# Nombres de siempre, para la rejilla BASE.
PASO, LATS, LONS, NLAT, NLON = BASE.paso, BASE.lats, BASE.lons, BASE.nlat, BASE.nlon
RUTA_AMBIENTE = BASE.ruta


def indice_mes(d: date) -> int:
    """Índice del mes de `d` en el eje de tiempo (el día se ignora)."""
    return (d.year - ORIGEN.year) * 12 + d.month - ORIGEN.month


def mes_indice(i: int) -> date:
    """Día 1 del mes con índice `i`."""
    a, m = divmod(ORIGEN.month - 1 + int(i), 12)
    return date(ORIGEN.year + a, m + 1, 1)


def sumar_meses(d: date, n: int) -> date:
    return mes_indice(indice_mes(d) + n)


def indices_celda(lat, lon, r: Rejilla = BASE) -> tuple[np.ndarray, np.ndarray]:
    """Fila y columna de la celda que contiene cada punto. Admite longitudes en -180..180 o 0..360."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    i = np.clip(np.floor((lat + 90) / r.paso).astype(int), 0, r.nlat - 1)
    j = np.floor(((lon + 180) % 360) / r.paso).astype(int) % r.nlon
    return i, j


def a_rejilla(valores: np.ndarray, lats: np.ndarray, lons: np.ndarray, r: Rejilla = BASE) -> np.ndarray:
    """
    Media por celda de una rejilla de origen cualquiera: `valores` (len(lats), len(lons)),
    con NaN donde no hay dato. Devuelve (nlat, nlon) float32, NaN en celdas sin ningún punto.
    """
    i = indices_celda(lats, 0, r)[0]
    j = indices_celda(0, lons, r)[1]
    ii, jj = np.meshgrid(i, j, indexing="ij")
    ok = ~np.isnan(valores)
    suma = np.zeros((r.nlat, r.nlon))
    cuenta = np.zeros((r.nlat, r.nlon))
    np.add.at(suma, (ii[ok], jj[ok]), valores[ok])
    np.add.at(cuenta, (ii[ok], jj[ok]), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cuenta > 0, suma / cuenta, np.nan).astype("float32")


def ampliar(campo: np.ndarray, factor: int = FACTOR_FINO) -> np.ndarray:
    """Pasa un campo (..., lat, lon) de la rejilla BASE a la FINA repitiendo cada celda factor x factor."""
    return np.repeat(np.repeat(campo, factor, axis=-2), factor, axis=-1)


# --------------------------------------------------------------------------- escritura

def abrir_escritura(variables: list[str], unidades: dict[str, str], r: Rejilla = BASE) -> zarr.Group:
    """Abre el almacén de la rejilla para escribir, creándolo con las variables que falten."""
    DATOS_DIR.mkdir(parents=True, exist_ok=True)
    # Sin metadatos consolidados: al escribir hay que ver las variables recién creadas.
    g = zarr.open_group(r.ruta, mode="a", use_consolidated=False)
    if "time" not in g:
        dias = np.array([(mes_indice(i) - ORIGEN).days for i in range(NT)], dtype="int32")
        g.create_array("time", data=dias, dimension_names=["time"],
                       attributes={"units": f"days since {ORIGEN.isoformat()}", "calendar": "proleptic_gregorian"})
        g.create_array("lat", data=r.lats, dimension_names=["lat"], attributes={"units": "degrees_north"})
        g.create_array("lon", data=r.lons, dimension_names=["lon"], attributes={"units": "degrees_east"})
        g.create_array("mar", data=np.zeros((r.nlat, r.nlon), dtype=bool), dimension_names=["lat", "lon"],
                       attributes={"descripcion": "celda de mar", "paso_grados": r.paso})
    for v in variables:
        if v not in g:
            g.create_array(v, shape=(NT, r.nlat, r.nlon), dtype="float32", fill_value=np.nan,
                           chunks=(TROZO_TIEMPO, min(TROZO_ESPACIO, r.nlat), min(TROZO_ESPACIO, r.nlon)),
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


def consolidar(r: Rejilla = BASE) -> None:
    """Actualiza los metadatos consolidados para que los lectores vean los cambios."""
    if r.ruta.exists():
        zarr.consolidate_metadata(r.ruta)


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

def abrir_grupo(r: Rejilla = BASE) -> zarr.Group | None:
    """El almacén mensual de la rejilla, para leer con zarr (sin metadatos consolidados)."""
    if not r.ruta.exists():
        return None
    return zarr.open_group(r.ruta, mode="r", use_consolidated=False)


def rejilla_de(variable: str) -> Rejilla | None:
    """Rejilla en cuyo almacén está guardada la variable (la FINA tiene prioridad)."""
    for r in (FINA, BASE):
        g = abrir_grupo(r)
        if g is not None and variable in g:
            return r
    return None


def meses_anios(g: zarr.Group, variable: str, anios: np.ndarray, a: int, b: int) -> np.ndarray:
    """Medias mensuales (años, 12, b - a, nlon) de las filas a..b de una variable."""
    t0 = indice_mes(date(int(anios[0]), 1, 1))
    t1 = indice_mes(date(int(anios[-1]), 12, 1)) + 1
    m = g[variable][t0:t1, a:b, :]
    return m.reshape(len(anios), 12, b - a, m.shape[-1])


def medias_anuales(g: zarr.Group, variable: str, anios: np.ndarray, meses_minimos: int = 9,
                   franja: int = 45) -> np.ndarray:
    """
    Media anual (años, lat, lon) de una variable de un almacén mensual, sólo donde hay al
    menos `meses_minimos` meses con dato. Se lee por franjas de latitud: poca RAM.
    """
    nlat, nlon = g[variable].shape[1:]
    anual = np.full((len(anios), nlat, nlon), np.nan, dtype="float32")
    for a in range(0, nlat, franja):
        b = min(a + franja, nlat)
        m = meses_anios(g, variable, anios, a, b)
        cuenta = (~np.isnan(m)).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            media = np.nansum(m, axis=1) / cuenta
        anual[:, a:b, :] = np.where(cuenta >= meses_minimos, media, np.nan)
    return anual


def abrir_ambiente(r: Rejilla = BASE) -> xr.Dataset | None:
    if not r.ruta.exists():
        return None
    return xr.open_zarr(r.ruta, consolidated=None)


def celda_mas_cercana(mar: np.ndarray, lat: float, lon: float, max_km: float | None = None,
                      r: Rejilla = BASE) -> tuple[int, int] | None:
    """Celda de mar más cercana a (lat, lon), o None si está a más de `max_km` (por defecto, 1,5 celdas)."""
    ii, jj = np.nonzero(mar)
    if not len(ii):
        return None
    d = distancia_km(lat, lon, r.lats[ii], r.lons[jj])
    n = int(np.argmin(d))
    if d[n] > (max_km if max_km is not None else 1.5 * r.paso * 111.2):
        return None
    return int(ii[n]), int(jj[n])


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia del círculo máximo; admite arrays de numpy."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))
