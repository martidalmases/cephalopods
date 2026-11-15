"""
Almacén de HÁBITAT: las cuatro variables elegidas para las especies, AÑO A AÑO, en una
rejilla mundial de 0,25° (720 x 1440 celdas). Lo usan especies_habitat.py (perfil de cada
especie y mapa de idoneidad) y exportar_trends.py (tendencias de estas variables en la web).

    sst_winter_min   temperatura del mes más frío del año            MERRA-2 TS, desde 1980
    sst_summer_max   temperatura del mes más cálido del año          MERRA-2 TS, desde 1980
    mhw_months       meses anómalamente cálidos: por encima del      MERRA-2 TS, desde 1980
                     percentil 90 de ese mes del calendario
    chl              clorofila-a, media anual                        MODIS-Aqua, desde 2003

Qué resolución es real:
  - La temperatura sale de MERRA-2, cuya rejilla es de 0,5° x 0,625°. A 0,25° cada celda toma
    el valor de la celda de MERRA-2 de mar que la contiene (o la vecina de mar, en la costa):
    NO hay más detalle que el de MERRA-2, sólo la misma rejilla que el resto.
  - La clorofila sí es de 0,25° reales: se promedian píxeles de 4 km de MODIS.

Por qué no sale del almacén mensual de 1° (ambiente.zarr): el mes más frío, el más cálido y los
meses cálidos hay que calcularlos sobre los meses en la rejilla original, no sobre medias de 1°.

Ficheros (DATOS_DIR/habitat/, sólo numpy):
    ts/AAAA.npy            (12, 361, 576)  TS mensual de MERRA-2 en °C, rejilla original
    anual/<var>/AAAA.npy   (720, 1440)     valor anual de cada variable; NaN en tierra o sin dato
    mar.npy, origen_ts.npy                 máscara de mar y celda de MERRA-2 de cada celda
    meta.json                              años disponibles de cada variable y versión

Se actualiza al final del recolector. A mano:

    python habitat.py                 # descarga lo que falte y recalcula
    python habitat.py --sin-clorofila # sólo temperatura (rápido: ~1 GB de texto en total)

Primera vez: unas 560 peticiones a MERRA-2 (un mes cada una) y 276 a ERDDAP (un mes de
clorofila cada una, ~17 MB). Ocupa en disco ~0,5 GB (ts) + ~0,8 GB (anual).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

import regiones

log = logging.getLogger("habitat")

DATOS_DIR = Path(os.environ.get("DATOS_DIR", Path(__file__).parent / "datos"))
DIR = DATOS_DIR / "habitat"
PASO = 0.25
NLAT, NLON = 720, 1440
LATS = -90 + PASO / 2 + PASO * np.arange(NLAT)        # centros, de sur a norte
LONS = -180 + PASO / 2 + PASO * np.arange(NLON)       # centros, de oeste a este
ANIO_INICIO = 1980
MESES_MINIMOS_CHL = 9          # meses con dato para que un año de clorofila cuente en una celda
PERCENTIL_CALIDO = 90
BASE_CALIDOS = None            # (año inicial, año final) del percentil; None = todos los años completos
PASO_MODIS = 3                 # un píxel de cada 3 (4 km -> 12,5 km): 2 x 2 muestras por celda de 0,25°
HILOS = 4
FRANJA = 60                    # filas de MERRA-2 por franja al derivar: memoria acotada

# MERRA-2: latitudes cada 0,5° desde -90 (361) y longitudes cada 0,625° desde -180 (576).
MERRA_NLAT, MERRA_NLON = 361, 576
MERRA_RECORTE = "[0:0][0:1:360][0:1:575]"

# Catálogo, con los textos de la web (en inglés, como el resto de la web).
VARIABLES = {
    "sst_winter_min": dict(name="Coldest-month sea temperature", unit="°C", decimals=2, desde=1980,
                           definition="Sea surface temperature of the coldest month of each year. It sets which "
                                      "warm-water species survive the winter.",
                           source="NASA MERRA-2 monthly (M2TMNXSLV TS), coldest month of the year"),
    "sst_summer_max": dict(name="Hottest-month sea temperature", unit="°C", decimals=2, desde=1980,
                           definition="Sea surface temperature of the hottest month of each year. It sets the heat "
                                      "stress that cold-water species must endure.",
                           source="NASA MERRA-2 monthly (M2TMNXSLV TS), hottest month of the year"),
    "mhw_months": dict(name="Unusually hot months", unit="months/yr", decimals=1, desde=1980,
                       definition="Months of the year warmer than the 90th percentile of that calendar month at that "
                                  "place. A monthly stand-in for marine heatwaves.",
                       source="NASA MERRA-2 monthly (M2TMNXSLV TS), months above the 90th percentile"),
    "chl": dict(name="Chlorophyll-a", unit="mg/m³", decimals=3, desde=2003,
                definition="The green pigment in microscopic algae: how much food there is at the base of the "
                           "food web.",
                source="NASA MODIS-Aqua monthly, 4 km (NOAA CoastWatch ERDDAP), yearly mean"),
}
TEMPERATURA = ["sst_winter_min", "sst_summer_max", "mhw_months"]


class ErrorHabitat(Exception):
    pass


# --------------------------------------------------------------------------- rejilla y ficheros

def indices(lat, lon) -> tuple[np.ndarray, np.ndarray]:
    """Fila y columna de la celda de 0,25° que contiene cada punto (lon en -180..180 o 0..360)."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    i = np.clip(np.floor((lat + 90) / PASO).astype(int), 0, NLAT - 1)
    j = np.floor(((lon + 180) % 360) / PASO).astype(int) % NLON
    return i, j


def a_rejilla(valores: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Suma y número de datos por celda de 0,25° de un campo (lat, lon) de cualquier rejilla más fina."""
    i, _ = indices(lats, 0.0)
    _, j = indices(0.0, lons)
    ok = np.isfinite(valores)
    plano = (i[:, None] * NLON + j[None, :])[ok]
    suma = np.bincount(plano, weights=valores[ok], minlength=NLAT * NLON).reshape(NLAT, NLON)
    cuenta = np.bincount(plano, minlength=NLAT * NLON).reshape(NLAT, NLON)
    return suma, cuenta


def _guardar(ruta: Path, a: np.ndarray) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp.npy")
    np.save(tmp, a)
    tmp.replace(ruta)


def ruta_anual(variable: str, anio: int) -> Path:
    return DIR / "anual" / variable / f"{anio}.npy"


def ruta_ts(anio: int) -> Path:
    return DIR / "ts" / f"{anio}.npy"


def leer_meta() -> dict | None:
    ruta = DIR / "meta.json"
    return json.loads(ruta.read_text()) if ruta.exists() else None


def anios_de(variable: str) -> list[int]:
    """Años con valor anual guardado de una variable."""
    carpeta = DIR / "anual" / variable
    if not carpeta.is_dir():
        return []
    return sorted(int(p.stem) for p in carpeta.glob("[0-9][0-9][0-9][0-9].npy"))


def mar() -> np.ndarray:
    ruta = DIR / "mar.npy"
    if not ruta.exists():
        raise ErrorHabitat("No hay almacén de hábitat: ejecuta `python habitat.py`")
    return np.load(ruta)


def anual(variable: str, anio: int) -> np.ndarray:
    """Mapa mundial (720, 1440) de un año; se abre sin cargarlo entero (mmap)."""
    return np.load(ruta_anual(variable, anio), mmap_mode="r")


def valores_en(variable: str, anio: np.ndarray, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Valor de la variable en cada (año, celda). NaN si no hay ese año o esa celda no tiene dato."""
    anio = np.asarray(anio)
    salida = np.full(len(anio), np.nan, dtype="float32")
    disponibles = set(anios_de(variable))
    for a in np.unique(anio):
        if int(a) in disponibles:
            de = anio == a
            salida[de] = anual(variable, int(a))[i[de], j[de]]
    return salida


def caja(variable: str, anios: list[int], region: dict = regiones.MEDITERRANEO) -> np.ndarray:
    """(años, ny, nx) de una región, con la fila 0 al NORTE como en la web. NaN en los años que falten."""
    i0, j0 = indices(region["lat"][0] + 1e-6, region["lon"][0] + 1e-6)
    ny = int(round((region["lat"][1] - region["lat"][0]) / PASO))
    nx = int(round((region["lon"][1] - region["lon"][0]) / PASO))
    salida = np.full((len(anios), ny, nx), np.nan, dtype="float32")
    disponibles = set(anios_de(variable))
    for k, a in enumerate(anios):
        if a in disponibles:
            salida[k] = anual(variable, a)[int(i0):int(i0) + ny, int(j0):int(j0) + nx][::-1]
    return salida


def rejilla_region(region: dict = regiones.MEDITERRANEO) -> dict:
    """Geometría de la caja de una región a 0,25°: centros (norte arriba), máscara y celdas de mar."""
    ny = int(round((region["lat"][1] - region["lat"][0]) / PASO))
    nx = int(round((region["lon"][1] - region["lon"][0]) / PASO))
    lat_c = region["lat"][1] - PASO * (np.arange(ny) + 0.5)
    lon_c = region["lon"][0] + PASO * (np.arange(nx) + 0.5)
    i0, j0 = indices(region["lat"][0] + 1e-6, region["lon"][0] + 1e-6)
    es_mar = mar()[int(i0):int(i0) + ny, int(j0):int(j0) + nx][::-1]
    dentro = region["mascara"](lat_c, lon_c) if region.get("mascara") else np.ones((ny, nx), bool)
    return {"ny": ny, "nx": nx, "lat": lat_c, "lon": lon_c, "mar": es_mar & dentro,
            "i0": int(i0), "j0": int(j0)}


def a_paso(mapa: np.ndarray, paso: float) -> np.ndarray:
    """Media por bloques de un mapa mundial de 0,25° a una rejilla más gruesa (1°, 2°...)."""
    f = int(round(paso / PASO))
    if abs(f * PASO - paso) > 1e-9 or NLAT % f:
        raise ErrorHabitat(f"El paso {paso} no es múltiplo de {PASO}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(np.asarray(mapa).reshape(NLAT // f, f, NLON // f, f), axis=(1, 3))


# --------------------------------------------------------------------------- temperatura (MERRA-2)

def origen_merra(fraccion_mar: np.ndarray) -> np.ndarray:
    """
    Para cada celda de 0,25°, el índice plano de la celda de MERRA-2 de la que toma la
    temperatura: la que la contiene si es de mar (>= 50 % de océano) y, si no, la vecina de mar
    más cercana (una celda de MERRA-2 de distancia como mucho). -1 si no hay ninguna: tierra.
    Sobre tierra TS es la temperatura del suelo, por eso no se usa nunca una celda de tierra.
    """
    es_mar = fraccion_mar >= 0.5
    la, lo = np.meshgrid(LATS, LONS, indexing="ij")
    ci = np.clip(np.rint((la + 90) / 0.5).astype(int), 0, MERRA_NLAT - 1)
    cj = np.rint((lo + 180) / 0.625).astype(int) % MERRA_NLON
    mejor = np.full(la.shape, -1, dtype="int32")
    mejor_d = np.full(la.shape, np.inf)
    for di in (0, -1, 1):
        for dj in (0, -1, 1):
            vi, vj = np.clip(ci + di, 0, MERRA_NLAT - 1), (cj + dj) % MERRA_NLON
            dlat = (-90 + 0.5 * vi) - la
            dlon = ((-180 + 0.625 * vj) - lo + 180) % 360 - 180
            d = np.hypot(dlat, dlon * np.cos(np.deg2rad(la)))
            d = np.where(es_mar[vi, vj], d + (0 if di == dj == 0 else 1e-6), np.inf)   # a igualdad, la propia
            cerca = d < mejor_d
            mejor = np.where(cerca, vi * MERRA_NLON + vj, mejor)
            mejor_d = np.minimum(d, mejor_d)
    return mejor.astype("int32")


def _descargar_fraccion_mar(token: str) -> np.ndarray:
    import recolector
    url = next(iter(recolector._granulos(short_name="M2C0NXASM", version="5.12.4", provider="GES_DISC").values()))
    return recolector._opendap(url, "FROCEAN" + MERRA_RECORTE, ["FROCEAN"], token)["FROCEAN"]


def _granulos_ts() -> dict[str, str]:
    import recolector
    return recolector._granulos(short_name="M2TMNXSLV", version="5.12.4", provider="GES_DISC")


def _descargar_ts_mes(url: str, token: str) -> np.ndarray:
    """TS de un mes en la rejilla de MERRA-2 (361, 576), en °C."""
    import recolector
    ts = recolector._opendap(url, "TS" + MERRA_RECORTE, ["TS"], token)["TS"]
    if ts.shape != (MERRA_NLAT, MERRA_NLON):
        raise ErrorHabitat(f"TS con forma inesperada {ts.shape}")
    return (ts - 273.15).astype("float32")


def actualizar_ts(token: str) -> bool:
    """Descarga los meses de TS que falten. Devuelve True si ha cambiado algo."""
    if not (DIR / "origen_ts.npy").exists():
        _guardar(DIR / "origen_ts.npy", origen_merra(_descargar_fraccion_mar(token)))
    granulos = _granulos_ts()
    cambios = False
    for anio in range(ANIO_INICIO, date.today().year + 1):
        ruta = ruta_ts(anio)
        vacio = [True] * 12
        if ruta.exists():
            guardado = np.load(ruta, mmap_mode="r")                 # sólo se mira una fila: el ecuador
            vacio = [not np.isfinite(guardado[m, MERRA_NLAT // 2]).any() for m in range(12)]
        faltan = [m for m in range(12) if f"{anio}-{m + 1:02d}" in granulos and vacio[m]]
        if not faltan:
            continue
        bloque = np.load(ruta) if ruta.exists() else np.full((12, MERRA_NLAT, MERRA_NLON), np.nan, dtype="float32")
        with ThreadPoolExecutor(max_workers=HILOS) as pool:
            futuros = {m: pool.submit(_descargar_ts_mes, granulos[f"{anio}-{m + 1:02d}"], token) for m in faltan}
        hechos = 0
        for m, futuro in futuros.items():
            try:
                bloque[m] = futuro.result()
                hechos += 1
            except Exception as e:  # un mes que falla se reintenta en la próxima ejecución
                log.warning("TS %d-%02d: %s (se reintentará)", anio, m + 1, e)
        if hechos:
            _guardar(ruta, bloque)
            cambios = True
        log.info("TS %d: %d meses nuevos", anio, hechos)
    return cambios


def anios_completos_ts() -> list[int]:
    """Años con los 12 meses de TS guardados."""
    completos = []
    for ruta in sorted((DIR / "ts").glob("[0-9][0-9][0-9][0-9].npy")):
        bloque = np.load(ruta, mmap_mode="r")
        if all(np.isfinite(bloque[m, MERRA_NLAT // 2]).any() for m in range(12)):
            completos.append(int(ruta.stem))
    return completos


def estadisticos_anuales(mensual: np.ndarray, base: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """
    De (años, 12, ...) a los tres valores anuales (años, ...). `base` marca los años que
    definen el percentil de cada mes del calendario (por defecto, todos).
    """
    ref = mensual if base is None else mensual[base]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # celdas sin dato
        p = np.nanpercentile(ref, PERCENTIL_CALIDO, axis=0)       # (12, ...)
        frio, calido = mensual.min(axis=1), mensual.max(axis=1)
    calidos = (mensual > p[None]).sum(axis=1).astype("float32")
    calidos[np.isnan(frio)] = np.nan
    return {"sst_winter_min": frio, "sst_summer_max": calido, "mhw_months": calidos}


def derivar_temperatura() -> list[int]:
    """Calcula y guarda las tres variables de temperatura de todos los años completos."""
    anios = anios_completos_ts()
    if len(anios) < 10:
        raise ErrorHabitat(f"Sólo hay {len(anios)} años completos de TS: hacen falta al menos 10")
    origen = np.load(DIR / "origen_ts.npy")
    bloques = [np.load(ruta_ts(a), mmap_mode="r") for a in anios]
    base = None
    if BASE_CALIDOS:
        base = np.array([BASE_CALIDOS[0] <= a <= BASE_CALIDOS[1] for a in anios])
    nativo = {v: np.full((len(anios), MERRA_NLAT, MERRA_NLON), np.nan, dtype="float32") for v in TEMPERATURA}
    for a in range(0, MERRA_NLAT, FRANJA):
        b = min(a + FRANJA, MERRA_NLAT)
        mensual = np.stack([np.asarray(bl[:, a:b, :]) for bl in bloques])       # (años, 12, filas, 576)
        for v, valores in estadisticos_anuales(mensual, base).items():
            nativo[v][:, a:b, :] = valores
    hay = origen >= 0
    for v in TEMPERATURA:
        for k, anio in enumerate(anios):
            mapa = np.full((NLAT, NLON), np.nan, dtype="float32")
            mapa[hay] = nativo[v][k].ravel()[origen[hay]]
            _guardar(ruta_anual(v, anio), mapa)
    log.info("Temperatura derivada: %d-%d", anios[0], anios[-1])
    return anios


# --------------------------------------------------------------------------- clorofila (MODIS)

def _descargar_chl_mes(mes: date) -> tuple[np.ndarray, np.ndarray] | None:
    """Suma y número de píxeles por celda de 0,25° de un mes de clorofila. None si ese mes no existe."""
    import xarray as xr
    import almacen
    import recolector
    fuente = next((f for f in recolector.ERDDAP_FUENTES["fitoplancton"]
                   if recolector._mes(f["desde"]) <= mes and (not f["hasta"] or mes <= recolector._mes(f["hasta"]))), None)
    if fuente is None:
        return None
    fin = date.fromordinal(almacen.sumar_meses(mes, 1).toordinal() - 1)
    url = (f"{fuente['servidor']}/griddap/{fuente['dataset']}.nc?{fuente['variable']}"
           f"[({mes}T00:00:00Z):1:({fin}T23:59:59Z)][0:{PASO_MODIS}:last][0:{PASO_MODIS}:last]")
    r = recolector._get(url, timeout=900)
    if r.status_code == 404 and "no matching results" in r.text:
        return None
    if r.status_code != 200:
        raise ErrorHabitat(f"ERDDAP {fuente['dataset']} HTTP {r.status_code}: {r.text[:200]}")
    with xr.open_dataset(io.BytesIO(r.content), engine="scipy") as ds:
        datos = ds[fuente["variable"]].mean("time").values.astype(float)       # un compuesto por mes
        return a_rejilla(datos, ds["latitude"].values, ds["longitude"].values)


def actualizar_chl() -> bool:
    """Media anual de clorofila de los años completos que falten (12 descargas por año)."""
    cambios = False
    visto_ruta = DIR / "chl_visto.npy"
    visto = np.load(visto_ruta) if visto_ruta.exists() else np.zeros((NLAT, NLON), bool)
    hechos = set(anios_de("chl"))
    for anio in range(VARIABLES["chl"]["desde"], date.today().year):
        if anio in hechos:
            continue
        with ThreadPoolExecutor(max_workers=2) as pool:        # ERDDAP es lento: dos a la vez
            futuros = [pool.submit(_descargar_chl_mes, date(anio, m, 1)) for m in range(1, 13)]
        try:
            meses = [f.result() for f in futuros]
        except Exception as e:  # el año entero se reintenta en la próxima ejecución
            log.warning("Clorofila %d: %s (se reintentará)", anio, e)
            continue
        suma, n_meses = np.zeros((NLAT, NLON)), np.zeros((NLAT, NLON), dtype=int)
        for mes in meses:
            if mes is not None:
                s, c = mes
                with np.errstate(invalid="ignore", divide="ignore"):
                    suma += np.where(c > 0, s / c, 0.0)
                n_meses += c > 0
        if not n_meses.any():
            log.warning("Clorofila %d: sin datos", anio)
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            media = np.where(n_meses >= MESES_MINIMOS_CHL, suma / n_meses, np.nan).astype("float32")
        visto |= n_meses > 0
        _guardar(ruta_anual("chl", anio), media)
        _guardar(visto_ruta, visto)
        cambios = True
        log.info("Clorofila %d guardada", anio)
    return cambios


# --------------------------------------------------------------------------- máscara, meta, todo

def _cerrar() -> dict:
    """Máscara de mar y meta.json a partir de lo que hay guardado."""
    origen = np.load(DIR / "origen_ts.npy")
    es_mar = origen >= 0
    if (DIR / "chl_visto.npy").exists():
        # MODIS ve la costa real a 4 km: quita las celdas de 0,25° que en realidad son tierra.
        visto = np.load(DIR / "chl_visto.npy")
        if visto.any():
            es_mar &= visto
    _guardar(DIR / "mar.npy", es_mar)
    anios = {v: anios_de(v) for v in VARIABLES}
    firma = hashlib.sha1(json.dumps(anios, sort_keys=True).encode() + str(int(es_mar.sum())).encode()).hexdigest()[:12]
    meta = {"paso": PASO, "anios": anios, "celdas_mar": int(es_mar.sum()), "version": firma,
            "percentil_calido": PERCENTIL_CALIDO, "base_calidos": BASE_CALIDOS,
            "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "meta.json").write_text(json.dumps(meta))
    return meta


def actualizar(token: str | None, clorofila: bool = True) -> dict:
    """Descarga lo que falte y recalcula. Devuelve meta.json."""
    if not token:
        raise ErrorHabitat("Falta EARTHDATA_TOKEN")
    antes = anios_de("sst_winter_min")
    cambia_ts = actualizar_ts(token)
    if cambia_ts or anios_completos_ts() != antes:
        derivar_temperatura()
    if clorofila:
        try:
            actualizar_chl()
        except Exception as e:  # la clorofila no debe impedir que haya temperatura
            log.error("Clorofila: %s", e)
    meta = _cerrar()
    log.info("Hábitat: %s", {v: (a[0], a[-1]) if a else None for v, a in meta["anios"].items()})
    return meta


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Almacén de hábitat a 0,25°")
    parser.add_argument("--sin-clorofila", action="store_true", help="sólo temperatura")
    args = parser.parse_args()
    from datos_mediterraneo import _token_earthdata
    actualizar(_token_earthdata(None), clorofila=not args.sin_clorofila)


if __name__ == "__main__":
    main()
