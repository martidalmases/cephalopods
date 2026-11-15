"""
Exporta los datos REALES (ambiente.zarr) al formato de la web Trend Detective
(trend-detective/README.md, «Data format»): por cada conjunto, un meta.json y un JSON por
variable con los valores anuales de cada celda de mar, su tendencia y la media de la región.

La web exige que todas las variables de un conjunto tengan los mismos años y sin huecos, y
las fuentes empiezan en años distintos (MERRA-2 1980, MODIS 2003, SMAP 2015). Por eso hay un
conjunto por región y por año de inicio, con las variables que cubren todo ese periodo:

    DATOS_DIR/trends/
      index.json                         lista de conjuntos (la usa el selector de la web)
      mediterraneo-1980/meta.json ...    Mediterráneo, celdas de 1°, 1980-último año completo
      mediterraneo-2003/ ...             + clorofila y fluorescencia
      mediterraneo-2015/ ...             + salinidad
      mundo-1980/ ...                    océano mundial, celdas de 2° (para que pese poco)
      ...

      habitat-1980/ ...                  Mediterráneo a 0,25° con las variables de especies (habitat.py)
      habitat-2003/ ...                  + clorofila a 0,25°

Tendencias: el mismo método en todo (tendencias.py): pendiente de Sen, Mann-Kendall modificado
(Hamed-Rao) y corrección FDR de Benjamini-Hochberg. En los mapas la corrección se hace sobre
las celdas; en la media de la región, sobre las variables del conjunto. Cada variable mensual
lleva además el detalle por mes del calendario de la media de la región (¿se calienta más el
verano que el invierno?) y la tendencia de la serie mensual sin el ciclo anual.

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
import zarr

import almacen
import habitat
import regiones
import tendencias

log = logging.getLogger("exportar_trends")

DIR_TRENDS = almacen.DATOS_DIR / "trends"
MESES_MINIMOS = 9
ANIOS_MINIMOS = 8            # un conjunto necesita al menos estos años

# Variables de la web: id de la web -> variable del recolector, con textos en inglés (la web lo está).
VARIABLES = [
    dict(id="sst", variable="temperatura", name="Sea surface temperature", unit="°C", decimals=2,
         definition="How warm the surface is (MERRA-2 skin temperature; over the sea, the sea surface).",
         source="NASA MERRA-2 monthly (M2TMNXSLV TS)"),
    dict(id="sst_winter_min", habitat="sst_winter_min", name="Coldest-month sea temperature", unit="°C", decimals=2,
         definition="Sea surface temperature of the coldest month of each year.",
         source="NASA MERRA-2 monthly (M2TMNXSLV TS), coldest month of the year"),
    dict(id="sst_summer_max", habitat="sst_summer_max", name="Hottest-month sea temperature", unit="°C", decimals=2,
         definition="Sea surface temperature of the hottest month of each year.",
         source="NASA MERRA-2 monthly (M2TMNXSLV TS), hottest month of the year"),
    dict(id="mhw_months", habitat="mhw_months", name="Unusually hot months (marine heatwaves)", unit="months/yr", decimals=1,
         definition="Months of the year warmer than the 90th percentile of that calendar month at that place. "
                    "A monthly stand-in for marine heatwaves.",
         source="NASA MERRA-2 monthly (M2TMNXSLV TS), months above the 90th percentile"),
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
    dict(id="q", variable="humedad_especifica", name="Specific humidity of the air", unit="g/kg", decimals=2,
         definition="Grams of water vapour in each kilogram of air just above the sea.",
         source="NASA MERRA-2 monthly (M2TMNXFLX QLML)"),
    dict(id="tqv", variable="vapor_agua", name="Total column water vapour", unit="kg/m²", decimals=2,
         definition="All the water vapour in the air column above the sea, from the surface to the top of the atmosphere.",
         source="NASA MERRA-2 monthly (M2TMNXSLV TQV)"),
]


# Regiones: caja (bordes de celda) y factor de agregación sobre la rejilla de almacen.py.
REGIONES = {
    "mediterraneo": {**regiones.MEDITERRANEO, "factor": 1},
    "mundo": {"name": "World ocean", "lat": (-78, 84), "lon": (-180, 180), "factor": 2, "mascara": None},
}


def _recortar(campo: np.ndarray, region: dict) -> np.ndarray:
    """(años, NLAT, NLON) -> (años, ny, nx) de la región, fila 0 = norte, celdas agregadas por `factor`."""
    f = region["factor"]
    i0, j0 = almacen.indices_celda(region["lat"][0] + 1e-6, region["lon"][0] + 1e-6)
    ny = int(round((region["lat"][1] - region["lat"][0]) / almacen.PASO))
    nx = int(round((region["lon"][1] - region["lon"][0]) / almacen.PASO))
    sub = campo[:, int(i0):int(i0) + ny, int(j0):int(j0) + nx]
    n = sub.shape[0]
    bloques = sub.reshape(n, ny // f, f, nx // f, f)
    with warnings.catch_warnings():          # bloques sin ningún dato: NaN, que es lo esperado
        warnings.simplefilter("ignore", RuntimeWarning)
        agregado = np.nanmean(bloques, axis=(2, 4))
    return agregado[:, ::-1, :]   # la web quiere el norte arriba


def trend_stats(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pendiente de Sen por década y p del test de Mann-Kendall modificado, por columna."""
    slope, p, _ = tendencias.tendencias(values)
    return slope, p


def _rnd(a, d: int) -> list[float]:
    return [round(float(v), d) for v in np.asarray(a).ravel()]


def _documento(var: dict, values: np.ndarray, lat_celdas: np.ndarray) -> dict:
    slope, p = trend_stats(values)
    w = np.cos(np.deg2rad(lat_celdas))
    regional = (values * w).sum(axis=1) / w.sum()
    rs, rp = trend_stats(regional[:, None])
    anom = values - values.mean(axis=0)
    d = var["decimals"]
    return {
        "id": var["id"],
        "values": [_rnd(fila, d) for fila in values],
        # p_adj: p ajustada por FDR sobre todas las celdas del mapa (la que decide el rayado).
        "trend": {"slope_per_decade": _rnd(slope, d + 1), "p": _rnd(p, 4), "p_adj": _rnd(tendencias.fdr(p), 4),
                  "method": tendencias.METODO},
        "regional": {"mean": _rnd(regional, d + 1), "slope_per_decade": round(float(rs[0]), d + 1),
                     "p": round(float(rp[0]), 4)},
        "scale": {"anomaly": round(float(np.percentile(np.abs(anom), 98)), d + 1) or 10 ** -d,
                  "slope": round(float(np.percentile(np.abs(slope), 98)), d + 1) or 10 ** -d},
    }


def _mensual_regional(g, variable: str, region: dict, mar: np.ndarray, anios: np.ndarray) -> np.ndarray:
    """Media mensual de la región (años, 12), ponderada por el área de las celdas de mar con dato."""
    i0, j0 = almacen.indices_celda(region["lat"][0] + 1e-6, region["lon"][0] + 1e-6)
    ny = int(round((region["lat"][1] - region["lat"][0]) / almacen.PASO))
    nx = int(round((region["lon"][1] - region["lon"][0]) / almacen.PASO))
    i0, j0 = int(i0), int(j0)
    lat, lon = almacen.LATS[i0:i0 + ny], almacen.LONS[j0:j0 + nx]
    dentro = mar[i0:i0 + ny, j0:j0 + nx] & (region["mascara"](lat, lon) if region.get("mascara") else True)
    peso = dentro * np.cos(np.deg2rad(lat))[:, None]
    t0 = almacen.indice_mes(date(int(anios[0]), 1, 1))
    t1 = almacen.indice_mes(date(int(anios[-1]), 12, 1)) + 1
    suma, total = np.zeros(t1 - t0), np.zeros(t1 - t0)
    for a in range(0, ny, 45):                       # por franjas de latitud: poca RAM
        b = min(a + 45, ny)
        m = g[variable][t0:t1, i0 + a:i0 + b, j0:j0 + nx]
        hay = np.isfinite(m) & (peso[a:b] > 0)
        suma += np.where(hay, m * peso[a:b], 0.0).sum(axis=(1, 2))
        total += (hay * peso[a:b]).sum(axis=(1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (suma / total).reshape(len(anios), 12)


def detalle_mensual(mensual: np.ndarray, decimales: int) -> dict | None:
    """
    Detalle mensual de la media de la región, a partir de (años, 12):
      - tendencia de cada mes del calendario (12 series anuales; FDR sobre los 12 meses);
      - tendencia de la serie mensual completa sin el ciclo anual (anomalías).
    """
    if np.isfinite(mensual).sum() < 12 * tendencias.MINIMO:
        return None
    slope, p, _ = tendencias.tendencias(mensual)                    # una columna por mes del calendario
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        clima = np.nanmean(mensual, axis=0)
    anomalia = (mensual - clima).ravel()
    b, pa = tendencias.tendencia(anomalia, np.arange(len(anomalia)) / 12.0)
    return {"climatology": _rnd(clima, decimales + 1),
            "slope_per_decade": [None if not np.isfinite(x) else round(float(x), decimales + 1) for x in slope],
            "p": _rnd(p, 4), "p_adj": _rnd(tendencias.fdr(p), 4),
            "anomaly": {"slope_per_decade": None if not np.isfinite(b) else round(b, decimales + 1), "p": round(pa, 4),
                        "n_months": int(np.isfinite(anomalia).sum())}}


def _escribir_conjunto(carpeta: Path, nombre: str, region: dict, paso: float, anios: list[int], mascara: np.ndarray,
                       variables: list[dict], campos: dict[str, np.ndarray], mensuales: dict[str, np.ndarray]) -> dict:
    """
    Escribe un conjunto: meta.json y un JSON por variable. `campos[id]` es (años, ny, nx) con el
    norte arriba; `mascara` (ny, nx) marca las celdas que entran. Devuelve la entrada del índice.
    """
    ny, nx = mascara.shape
    lat_c = region["lat"][1] - paso * (np.arange(ny) + 0.5)
    filas, cols = np.nonzero(mascara)
    carpeta.mkdir()
    docs = [_documento(v, campos[v["id"]][:, filas, cols], lat_c[filas]) for v in variables]
    # FDR de la media de la región: sobre las variables del conjunto (la tabla «todas las variables»).
    q = tendencias.fdr(np.array([d["regional"]["p"] for d in docs]))
    for v, doc, qv in zip(variables, docs, q):
        doc["regional"]["p_adj"] = round(float(qv), 4)
        if v["id"] in mensuales:
            try:
                detalle = detalle_mensual(mensuales[v["id"]], v["decimals"])
                if detalle:
                    doc["regional"]["monthly"] = detalle
            except Exception as e:  # el detalle mensual es un extra: no debe impedir exportar
                log.warning("%s %s: sin detalle mensual (%s)", nombre, v["id"], e)
        (carpeta / f"{v['id']}.json").write_text(json.dumps(doc, separators=(",", ":")))
    meta = {
        "synthetic": False,
        "region": {"name": region["name"], "lon_west": float(region["lon"][0]),
                   "lat_north": float(region["lat"][1]), "cell_deg": paso, "nx": int(nx), "ny": int(ny)},
        "years": [int(a) for a in anios],
        "method": tendencias.METODO,
        "ocean_index": [int(i) * int(nx) + int(j) for i, j in zip(filas, cols)],
        "variables": [{k: v[k] for k in ("id", "name", "unit", "decimals", "definition", "source")}
                      | {"file": v["id"] + ".json"} for v in variables],
    }
    (carpeta / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    log.info("%s: %d celdas, %d-%d, %s", nombre, len(filas), anios[0], anios[-1], [v["id"] for v in variables])
    return {"id": nombre, "region": region["name"], "desde": int(anios[0]), "hasta": int(anios[-1]),
            "celdas": int(len(filas)), "cell_deg": paso, "variables": [v["id"] for v in variables]}


def _habitat_a_paso(variable: str, anios: np.ndarray) -> np.ndarray | None:
    """Variable anual del almacén de hábitat (0,25°) promediada a la rejilla de almacen.py."""
    hay = set(habitat.anios_de(variable))
    if not hay:
        return None
    campo = np.full((len(anios), almacen.NLAT, almacen.NLON), np.nan, dtype="float32")
    for k, a in enumerate(anios):
        if int(a) in hay:
            campo[k] = habitat.a_paso(habitat.anual(variable, int(a)), almacen.PASO)
    return campo


def _conjuntos_habitat(tmp: Path) -> list[dict]:
    """Conjuntos del Mediterráneo a 0,25° con las variables de especies (si existe el almacén de hábitat)."""
    meta = habitat.leer_meta()
    if not meta:
        return []
    region = regiones.MEDITERRANEO
    g = habitat.rejilla_region(region)
    entradas = []
    catalogo = [dict(id=v, **{k: info[k] for k in ("name", "unit", "decimals", "definition", "source")})
                for v, info in habitat.VARIABLES.items()]
    for desde, ids in ((habitat.VARIABLES["sst_winter_min"]["desde"], habitat.TEMPERATURA),
                       (habitat.VARIABLES["chl"]["desde"], list(habitat.VARIABLES))):
        anios = [a for a in meta["anios"][ids[0]] if a >= desde and all(a in meta["anios"][v] for v in ids)]
        if len(anios) < ANIOS_MINIMOS:
            continue
        campos = {v: habitat.caja(v, anios, region) for v in ids}
        completas = g["mar"].copy()
        for v in ids:
            completas &= np.isfinite(campos[v]).all(axis=0)
        if completas.sum() < 10:
            continue
        entradas.append(_escribir_conjunto(tmp / f"habitat-{desde}", f"habitat-{desde}", region, habitat.PASO, anios,
                                           completas, [c for c in catalogo if c["id"] in ids], campos, {}))
    return entradas


def exportar() -> list[dict]:
    if not almacen.RUTA_AMBIENTE.exists():
        raise RuntimeError("No hay datos ambientales: ejecuta antes el recolector")
    g = zarr.open_group(almacen.RUTA_AMBIENTE, mode="r", use_consolidated=False)
    mar = g["mar"][:]
    anios = np.arange(almacen.ORIGEN.year, date.today().year)      # hasta el último año completo
    disponibles = [v for v in VARIABLES if v.get("variable") in g and g[v["variable"]].attrs.get("hasta")]

    # Medias anuales recortadas a cada región, variable a variable (poca RAM), y la media
    # mensual de la región para el detalle por meses.
    por_region: dict[str, dict[str, np.ndarray]] = {r: {} for r in REGIONES}
    mensual: dict[str, dict[str, np.ndarray]] = {r: {} for r in REGIONES}
    for v in disponibles:
        anual = almacen.medias_anuales(g, v["variable"], anios, MESES_MINIMOS)
        anual[:, ~mar] = np.nan
        for r, region in REGIONES.items():
            por_region[r][v["id"]] = _recortar(anual, region)
            try:
                mensual[r][v["id"]] = _mensual_regional(g, v["variable"], region, mar, anios)
            except Exception as e:  # el detalle mensual es un extra
                log.warning("%s %s: sin serie mensual (%s)", r, v["id"], e)
        del anual
    # Variables anuales que salen del almacén de hábitat (mes más frío, más cálido, meses cálidos).
    for v in [v for v in VARIABLES if v.get("habitat")]:
        try:
            anual = _habitat_a_paso(v["habitat"], anios)
        except Exception as e:
            log.warning("%s: no se pudo leer del almacén de hábitat (%s)", v["id"], e)
            continue
        if anual is not None:
            anual[:, ~mar] = np.nan
            for r, region in REGIONES.items():
                por_region[r][v["id"]] = _recortar(anual, region)
            del anual

    tmp = DIR_TRENDS.with_name("trends.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    indice = []
    for r, region in REGIONES.items():
        datos = por_region[r]
        f, paso = region["factor"], almacen.PASO * region["factor"]
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
            indice.append(_escribir_conjunto(tmp / nombre, nombre, region, paso, anios[k0:], completas, variables,
                                             {v["id"]: datos[v["id"]][k0:] for v in variables},
                                             {v["id"]: mensual[r][v["id"]][k0:] for v in variables if v["id"] in mensual[r]}))
    try:
        indice.extend(_conjuntos_habitat(tmp))
    except Exception as e:  # sin almacén de hábitat (o a medias) se exporta lo demás
        log.warning("Conjuntos de hábitat no exportados: %s", e)
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
