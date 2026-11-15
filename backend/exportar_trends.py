"""
Exporta los datos REALES (almacenes de almacen.py) al formato de la web Trend Detective
(trend-detective/README.md, «Data format»): por cada conjunto, un meta.json y un JSON por
variable con los valores anuales de cada celda de mar, su tendencia y la media de la región.

La web exige que todas las variables de un conjunto tengan los mismos años y sin huecos, y
las fuentes empiezan en años distintos (MERRA-2 1980, OISST 1982, MODIS 2003, SMAP 2015).
Por eso hay un conjunto por región y por año de inicio, con las variables que cubren todo
ese periodo. Todo se calcula en la rejilla FINA (0,5°); las variables de la BASE (1°) se
repiten en sus 4 celdas finas.

    DATOS_DIR/trends/
      index.json                         lista de conjuntos (la usa el selector de la web)
      mediterraneo-1980/meta.json ...    Mediterráneo, celdas de 0,5°, 1980-último año completo
      mediterraneo-1982/ ...             + temperatura del mar, mín./máx. anual y olas de calor
      mediterraneo-2003/ ...             + clorofila y fluorescencia
      mediterraneo-2015/ ...             + salinidad
      mundo-1980/ ...                    océano mundial, celdas de 2° (para que pese poco)
      ...

La API los sirve en /api/trends/<conjunto>/<fichero>. Se ejecuta al final del recolector, o:

    python exportar_trends.py
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
from scipy import stats

import almacen
import especies

log = logging.getLogger("exportar_trends")

DIR_TRENDS = almacen.DATOS_DIR / "trends"
MESES_MINIMOS = 9
ANIOS_MINIMOS = 8            # un conjunto necesita al menos estos años

# Variables de la web: id de la web -> variable del recolector, con textos en inglés (la web lo está).
VARIABLES = [
    dict(id="sst", variable="sst", name="Sea surface temperature", unit="°C", decimals=2,
         definition="How warm the top layer of the sea is (yearly mean).",
         source="NOAA OISST v2.1 monthly (0.25°)"),
    dict(id="sst_min", variable="sst_winter_min", name="Winter minimum sea temperature", unit="°C", decimals=2,
         definition="The temperature of the coldest month of the year. Many animals cannot survive water colder than their limit.",
         source="NOAA OISST v2.1 monthly (coldest monthly mean of each year)"),
    dict(id="sst_max", variable="sst_summer_max", name="Summer maximum sea temperature", unit="°C", decimals=2,
         definition="The temperature of the warmest month of the year. Heat stress starts above each species' limit.",
         source="NOAA OISST v2.1 monthly (warmest monthly mean of each year)"),
    dict(id="mhw", variable="mhw_months", name="Marine heatwave months", unit="months", decimals=1,
         definition="Months of the year warmer than 9 in 10 of the same month in 1982–2011: a monthly version of a marine heatwave.",
         source="NOAA OISST v2.1 monthly (above the 1982–2011 90th percentile of that month)"),
    dict(id="chl", variable="fitoplancton", name="Chlorophyll-a", unit="mg/m³", decimals=3,
         definition="The green pigment in microscopic algae. More chlorophyll means more algae in the water.",
         source="MODIS Aqua monthly (NOAA CoastWatch ERDDAP)"),
    dict(id="nflh", variable="fluorescencia", name="Fluorescence line height", unit="W/m²/µm/sr", decimals=4,
         definition="A faint red glow given off by algae. Useful near coasts, where chlorophyll readings are less reliable.",
         source="MODIS Aqua monthly (NOAA CoastWatch ERDDAP)"),
    dict(id="aod", variable="aerosoles", name="Aerosol optical depth", unit="", decimals=3,
         definition="How hazy the air is from dust, smoke and pollution. Higher means less sunlight reaches the sea.",
         source="NASA MERRA-2 monthly (M2TMNXAER TOTEXTTAU)"),
    dict(id="sss", variable="salinidad", name="Sea surface salinity", unit="PSU", decimals=3,
         definition="How salty the surface water is. It rises where evaporation beats rain and rivers.",
         source="NASA SMAP RSS v6 monthly (PO.DAAC)"),
    dict(id="wind", variable="viento_velocidad", name="Surface wind speed", unit="m/s", decimals=2,
         definition="How fast the air moves just above the sea. Wind mixes the water and drives currents.",
         source="NASA MERRA-2 monthly (M2TMNXFLX SPEED)"),
    dict(id="precip", variable="precipitacion", name="Precipitation", unit="mm/day", decimals=3,
         definition="Rain falling on the sea. It freshens the surface water.",
         source="NASA MERRA-2 monthly (M2TMNXFLX PRECTOTCORR)"),
    dict(id="evap", variable="evaporacion", name="Evaporation", unit="mm/day", decimals=3,
         definition="Water the sea loses to the air. It makes the surface saltier and cools it.",
         source="NASA MERRA-2 monthly (M2TMNXFLX EVAP)"),
    dict(id="lhf", variable="calor_latente", name="Latent heat flux", unit="W/m²", decimals=1,
         definition="Heat the sea loses by evaporation (positive = the sea loses heat).",
         source="NASA MERRA-2 monthly (M2TMNXFLX EFLUX)"),
    dict(id="shf", variable="calor_sensible", name="Sensible heat flux", unit="W/m²", decimals=1,
         definition="Heat the sea gives directly to the air above it (positive = the sea loses heat).",
         source="NASA MERRA-2 monthly (M2TMNXFLX HFLUX)"),
    dict(id="dtsa", variable="dif_temp_mar_aire", name="Sea–air temperature difference", unit="°C", decimals=2,
         definition="Surface temperature minus air temperature at 2 m. Positive means the sea warms the air.",
         source="NASA MERRA-2 monthly (M2TMNXSLV TS − T2M)"),
    dict(id="dqsa", variable="dif_humedad_mar_aire", name="Sea–air humidity difference", unit="g/kg", decimals=2,
         definition="How much drier the air is than saturated air at the sea temperature. It drives evaporation.",
         source="NASA MERRA-2 monthly (qsat(TS) − M2TMNXFLX QLML)"),
]


def _mascara_mediterraneo(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Sin Atlántico, golfo de Vizcaya ni mar Negro/Mármara (sobre centros de celda)."""
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    return ~((lo < -5.5) | ((la > 43.0) & (lo < 0.0)) | ((la > 40.0) & (lo > 26.5)))


# Regiones: caja (bordes de celda) y factor de agregación sobre la rejilla FINA (0,5°).
RF = almacen.FINA
REGIONES = {
    "mediterraneo": {"name": "Mediterranean Sea", "lat": (30, 46), "lon": (-6, 37), "factor": 1,
                     "mascara": _mascara_mediterraneo},
    "mundo": {"name": "World ocean", "lat": (-78, 84), "lon": (-180, 180), "factor": 4, "mascara": None},
}


def _recortar(campo: np.ndarray, region: dict) -> np.ndarray:
    """(años, lat, lon) FINA -> (años, ny, nx) de la región, fila 0 = norte, celdas agregadas por `factor`."""
    f = region["factor"]
    i0, j0 = almacen.indices_celda(region["lat"][0] + 1e-6, region["lon"][0] + 1e-6, RF)
    ny = int(round((region["lat"][1] - region["lat"][0]) / RF.paso))
    nx = int(round((region["lon"][1] - region["lon"][0]) / RF.paso))
    sub = campo[:, int(i0):int(i0) + ny, int(j0):int(j0) + nx]
    n = sub.shape[0]
    bloques = sub.reshape(n, ny // f, f, nx // f, f)
    with warnings.catch_warnings():          # bloques sin ningún dato: NaN, que es lo esperado
        warnings.simplefilter("ignore", RuntimeWarning)
        agregado = np.nanmean(bloques, axis=(2, 4))
    return agregado[:, ::-1, :]   # la web quiere el norte arriba


def trend_stats(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pendiente por década (mínimos cuadrados) y p de un test t bilateral, por columna (como la web)."""
    n = values.shape[0]
    t = np.arange(n) - (n - 1) / 2.0
    sxx = (t ** 2).sum()
    mean = values.mean(axis=0)
    b = (t[:, None] * (values - mean)).sum(axis=0) / sxx
    resid = values - (mean + np.outer(t, b))
    with np.errstate(invalid="ignore", divide="ignore"):
        se = np.sqrt((resid ** 2).sum(axis=0) / (n - 2) / sxx)
        p = 2 * stats.t.sf(np.abs(b / se), n - 2)
    return b * 10, np.nan_to_num(p, nan=1.0)


def _rnd(a, d: int) -> list[float]:
    return [round(float(v), d) for v in np.asarray(a).ravel()]


def _escribir_variable(carpeta: Path, var: dict, values: np.ndarray, lat_celdas: np.ndarray) -> None:
    slope, p = trend_stats(values)
    w = np.cos(np.deg2rad(lat_celdas))
    regional = (values * w).sum(axis=1) / w.sum()
    rs, rp = trend_stats(regional[:, None])
    anom = values - values.mean(axis=0)
    d = var["decimals"]
    doc = {
        "id": var["id"],
        "values": [_rnd(fila, d) for fila in values],
        "trend": {"slope_per_decade": _rnd(slope, d + 1), "p": _rnd(p, 4),
                  "method": "least-squares slope, two-sided t-test"},
        "regional": {"mean": _rnd(regional, d + 1), "slope_per_decade": round(float(rs[0]), d + 1),
                     "p": round(float(rp[0]), 4)},
        "scale": {"anomaly": round(float(np.percentile(np.abs(anom), 98)), d + 1) or 10 ** -d,
                  "slope": round(float(np.percentile(np.abs(slope), 98)), d + 1) or 10 ** -d},
    }
    (carpeta / f"{var['id']}.json").write_text(json.dumps(doc, separators=(",", ":")))


def exportar() -> list[dict]:
    g_base, g_fino = almacen.abrir_grupo(almacen.BASE), almacen.abrir_grupo(almacen.FINA)
    if g_base is None and g_fino is None:
        raise RuntimeError("No hay datos ambientales: ejecuta antes el recolector")
    mar = especies._mar()
    anios = np.arange(almacen.ORIGEN.year, date.today().year)      # hasta el último año completo

    def con_datos(g, v):
        return g is not None and v in g and bool(g[v].attrs.get("hasta"))

    # Extremos de la temperatura del mar (una sola pasada para los tres).
    extremos = especies.extremos_sst(g_fino, anios) if con_datos(g_fino, "sst") else {}

    def anual_de(v: str) -> np.ndarray | None:
        """Serie anual en la rejilla FINA (las variables de la BASE se repiten en 2 x 2 celdas)."""
        if v in extremos:
            return extremos[v]
        if con_datos(g_fino, v):
            return almacen.medias_anuales(g_fino, v, anios, MESES_MINIMOS)
        if con_datos(g_base, v):
            return almacen.ampliar(almacen.medias_anuales(g_base, v, anios, MESES_MINIMOS))
        return None

    # Medias anuales recortadas a cada región, variable a variable (poca RAM).
    por_region: dict[str, dict[str, np.ndarray]] = {r: {} for r in REGIONES}
    for v in VARIABLES:
        anual = anual_de(v["variable"])
        if anual is None:
            continue
        anual[:, ~mar] = np.nan
        for r, region in REGIONES.items():
            por_region[r][v["id"]] = _recortar(anual, region)
        del anual
    extremos.clear()

    tmp = DIR_TRENDS.with_name("trends.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    indice = []
    for r, region in REGIONES.items():
        datos = por_region[r]
        f, paso = region["factor"], RF.paso * region["factor"]
        ny, nx = next(iter(datos.values())).shape[1:] if datos else (0, 0)
        lat_c = region["lat"][1] - paso * (np.arange(ny) + 0.5)
        lon_c = region["lon"][0] + paso * (np.arange(nx) + 0.5)
        mascara = region["mascara"](lat_c, lon_c) if region["mascara"] else np.ones((ny, nx), bool)
        # Año desde el que cada variable tiene datos todos los años (en alguna celda).
        inicio = {}
        for vid, arr in datos.items():
            hay = np.isfinite(arr).any(axis=(1, 2))
            if hay[-1]:
                k = len(hay) - 1
                while k > 0 and hay[k - 1]:
                    k -= 1
                inicio[vid] = int(anios[k])
        for desde in sorted(set(inicio.values())):
            variables = [v for v in VARIABLES if inicio.get(v["id"], 9999) <= desde]
            k0 = int(np.searchsorted(anios, desde))
            if len(anios) - k0 < ANIOS_MINIMOS or not variables:
                continue
            # Celdas de mar con serie completa en todas las variables del conjunto.
            completas = mascara.copy()
            for v in variables:
                completas &= np.isfinite(datos[v["id"]][k0:]).all(axis=0)
            if completas.sum() < 10:
                continue
            nombre = f"{r}-{desde}"
            carpeta = tmp / nombre
            carpeta.mkdir()
            filas, cols = np.nonzero(completas)
            for v in variables:
                _escribir_variable(carpeta, v, datos[v["id"]][k0:][:, filas, cols], lat_c[filas])
            meta = {
                "synthetic": False,
                "region": {"name": region["name"], "lon_west": float(region["lon"][0]),
                           "lat_north": float(region["lat"][1]), "cell_deg": paso, "nx": int(nx), "ny": int(ny)},
                "years": [int(a) for a in anios[k0:]],
                "ocean_index": [int(i) * int(nx) + int(j) for i, j in zip(filas, cols)],
                "variables": [{k: v[k] for k in ("id", "name", "unit", "decimals", "definition", "source")}
                              | {"file": v["id"] + ".json"} for v in variables],
            }
            (carpeta / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
            indice.append({"id": nombre, "region": region["name"], "desde": desde, "hasta": int(anios[-1]),
                           "celdas": int(completas.sum()), "cell_deg": paso,
                           "variables": [v["id"] for v in variables]})
            log.info("%s: %d celdas, %d-%d, %s", nombre, int(completas.sum()), desde, anios[-1],
                     [v["id"] for v in variables])
    (tmp / "index.json").write_text(json.dumps(indice, separators=(",", ":")))
    viejo = DIR_TRENDS.with_name("trends.old")
    shutil.rmtree(viejo, ignore_errors=True)
    if DIR_TRENDS.exists():
        DIR_TRENDS.rename(viejo)
    tmp.rename(DIR_TRENDS)
    shutil.rmtree(viejo, ignore_errors=True)
    return indice


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    exportar()
