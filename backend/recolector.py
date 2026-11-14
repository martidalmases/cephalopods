"""
Recolector periódico: guarda la MEDIA MENSUAL de cada variable en la rejilla MUNDIAL de
almacen.py (1° por defecto), desde enero de 1980 hasta el último mes publicado.
Pensado para ejecutarse cada día con un temporizador de systemd o cron. En cada ejecución
pide sólo los meses que faltan en todo el periodo (también los huecos de ejecuciones que
fallaron) y repasa los últimos, que las fuentes NRT corrigen.

Fuentes (todas descargan el planeta entero de un mes en una sola petición):
  * MERRA-2 mensual (M2TMNXSLV, M2TMNXFLX, M2TMNXAER), 1980-hoy, por OPeNDAP de la NASA:
    temperatura, evaporación, precipitación, flujos de calor, viento, aerosoles y
    diferencias mar-aire.
    Su fracción de océano (M2C0NXASM) da la máscara de mar.
  * SMAP mensual (RSS v6, PO.DAAC), 2015-hoy, por OPeNDAP de la NASA: salinidad.
  * MODIS Aqua mensual por NOAA CoastWatch ERDDAP, 2003-hoy: clorofila y fluorescencia.
Las fuentes de la NASA requieren EARTHDATA_TOKEN. Antes de las fechas de inicio de cada
satélite no hay datos: esos meses quedan vacíos.

Memoria: se procesa un mes y una fuente cada vez y se escribe por bloques de un año; el
pico de RAM es de unas decenas de MB.

Uso:
    python recolector.py                       # actualización incremental (todas)
    python recolector.py --desde 2020-01-01    # volver a pedir todo desde un mes
    python recolector.py --variables salinidad fitoplancton
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import numpy as np
import requests
import xarray as xr

import almacen
from datos_mediterraneo import TIMEOUT_ERDDAP, VARIABLES, ErrorDatos, _humedad_saturacion_mar, _token_earthdata

log = logging.getLogger("recolector")

VARIABLES_RECOLECTADAS = ["temperatura", "salinidad", "fitoplancton", "fluorescencia", "aerosoles",
                          "evaporacion", "precipitacion", "calor_latente", "calor_sensible", "viento_velocidad",
                          "viento_direccion", "dif_temp_mar_aire", "dif_humedad_mar_aire"]
SOLAPE_MESES = 2               # se vuelven a pedir los últimos meses (productos NRT que se corrigen)
HILOS = 4                      # peticiones simultáneas
OPENDAP = "https://opendap.earthdata.nasa.gov"
CMR = "https://cmr.earthdata.nasa.gov/search"

# --------------------------------------------------------------------------- catálogo

# MERRA-2 mensual: colección -> variables de MERRA que se piden en una sola petición.
MERRA_COLECCIONES = {
    "M2TMNXSLV": ["TS", "T2M"],
    "M2TMNXFLX": ["EVAP", "EFLUX", "HFLUX", "SPEED", "ULML", "VLML", "QLML", "PRECTOTCORR"],
    "M2TMNXAER": ["TOTEXTTAU", "DUEXTTAU"],
}
# Variable del proyecto -> cálculo a partir de las variables de MERRA del mes (dict de arrays).
MERRA_VARIABLES = {
    "temperatura": lambda m: m["TS"] - 273.15,
    "dif_temp_mar_aire": lambda m: m["TS"] - m["T2M"],
    "dif_humedad_mar_aire": lambda m: (_qsat(m["TS"]) - m["QLML"]) * 1000,
    "evaporacion": lambda m: m["EVAP"] * 86400,                         # kg m-2 s-1 -> mm/día
    "precipitacion": lambda m: m["PRECTOTCORR"] * 86400,                # kg m-2 s-1 -> mm/día
    "calor_latente": lambda m: m["EFLUX"],
    "calor_sensible": lambda m: m["HFLUX"],
    "viento_velocidad": lambda m: m["SPEED"],
    "viento_direccion": None,          # se calcula aparte: media vectorial (ver _a_rejilla_merra)
    "aerosoles": lambda m: m["TOTEXTTAU"],
    "polvo_sahariano": lambda m: m["DUEXTTAU"],
}
_qsat = np.vectorize(_humedad_saturacion_mar, otypes=[float])

# MERRA-2: latitudes cada 0,5° desde -90 (361) y longitudes cada 0,625° desde -180 (576).
# Con celdas de 1° basta una latitud de cada dos (justo los centros, -89.5 ... 89.5).
_MERRA_PASO_LAT = 2 if almacen.PASO >= 1 else 1
_MERRA_I0 = 1 if _MERRA_PASO_LAT == 2 else 0
_MERRA_LATS = -90 + 0.5 * np.arange(_MERRA_I0, 361, _MERRA_PASO_LAT)
_MERRA_LONS = -180 + 0.625 * np.arange(576)
_MERRA_RECORTE = f"[0:0][{_MERRA_I0}:{_MERRA_PASO_LAT}:360][0:1:575]"

# SMAP RSS v6 mensual (PO.DAAC): rejilla de 0,25°, lat -89.875..89.875, lon 0.125..359.875.
SMAP = {"concepto": "C2832226365-POCLOUD", "variable": "sss_smap", "desde": "2015-04"}
_SMAP_LATS = -89.875 + 0.25 * np.arange(720)
_SMAP_LONS = 0.125 + 0.25 * np.arange(1440)

# ERDDAP (MODIS Aqua 4 km mensual): tramos de datasets por variable. Se toma un píxel de
# cada PASO_MODIS en cada eje (16 por celda de 1°) y se promedian por celda.
PASO_MODIS = max(1, int(round(almacen.PASO * 24 / 4)))
ERDDAP_FUENTES = {
    "fitoplancton": [
        {"servidor": "https://coastwatch.pfeg.noaa.gov/erddap", "dataset": "erdMH1chlamday", "variable": "chlorophyll",
         "desde": "2003-01", "hasta": "2022-02"},
        {"servidor": "https://coastwatch.pfeg.noaa.gov/erddap", "dataset": "erdMH1chlamday_R2022NRT",
         "variable": "chlorophyll", "desde": "2022-03", "hasta": None},
    ],
    "fluorescencia": [
        {"servidor": "https://coastwatch.pfeg.noaa.gov/erddap", "dataset": "erdMH1cflhmday", "variable": "cflh",
         "desde": "2003-01", "hasta": "2022-02"},
        {"servidor": "https://coastwatch.pfeg.noaa.gov/erddap", "dataset": "erdMH1cflhmday_R2022NRT",
         "variable": "nflh", "desde": "2022-03", "hasta": None},
    ],
}

# Descripción de la fuente de cada variable (para la API).
FUENTES = {
    **{v: {"fuente": "NASA MERRA-2 mensual (OPeNDAP)", "desde": "1980-01"} for v in MERRA_VARIABLES},
    "salinidad": {"fuente": "NASA SMAP RSS v6 mensual (PO.DAAC, OPeNDAP)", "desde": SMAP["desde"]},
    **{v: {"fuente": "NOAA CoastWatch ERDDAP, MODIS Aqua mensual: " + " + ".join(f["dataset"] for f in fs),
           "desde": fs[0]["desde"]} for v, fs in ERDDAP_FUENTES.items()},
}


def _mes(texto: str) -> date:
    return date(int(texto[:4]), int(texto[5:7]), 1)


def _get(url: str, intentos: int = 3, **kwargs) -> requests.Response:
    """GET con reintentos ante errores temporales (las fuentes devuelven 5xx/429 a veces)."""
    for i in range(intentos):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
        except requests.RequestException:
            if i == intentos - 1:
                raise
        time.sleep(15 * (i + 1))
    return r


def _bloque_vacio(n_meses: int) -> np.ndarray:
    return np.full((n_meses, almacen.NLAT, almacen.NLON), np.nan, dtype="float32")


def meses_pendientes(g, variables: list[str], inicio: date, fin: date, forzar: date | None) -> list[date]:
    """
    Meses de [inicio, fin] que hay que pedir: los que no tienen ningún dato en alguna de
    las variables, los SOLAPE_MESES últimos y, con --desde, todos a partir de esa fecha.
    Se comprueba por bloques de un año para no cargar en memoria toda la serie mundial.
    """
    inicio = max(inicio, almacen.ORIGEN)
    if inicio > fin:
        return []
    t0, t1 = almacen.indice_mes(inicio), almacen.indice_mes(fin) + 1
    vacio = np.zeros(t1 - t0, dtype=bool)
    for v in variables:
        for a in range(t0, t1, 12):
            b = min(a + 12, t1)
            vacio[a - t0:b - t0] |= np.isnan(g[v][a:b]).all(axis=(1, 2))
    meses = []
    for k in range(t1 - t0):
        mes = almacen.mes_indice(t0 + k)
        if vacio[k] or t1 - 1 - (t0 + k) < SOLAPE_MESES or (forzar and mes >= forzar):
            meses.append(mes)
    return meses


def _escribir_por_anios(g, resultados: dict[str, dict[date, np.ndarray]]) -> dict[str, int]:
    """
    Escribe {variable: {mes: mapa (lat, lon)}} en bloques de un año. Sólo se guardan las
    celdas de mar: la tierra (un tercio del planeta en MERRA-2) no se usa y ocuparía disco.
    """
    tierra = ~g["mar"][:]
    totales = {}
    for v, por_mes in resultados.items():
        totales[v] = 0
        for anio in sorted({m.year for m in por_mes}):
            bloque = _bloque_vacio(12)
            for m, mapa in por_mes.items():
                if m.year == anio:
                    bloque[m.month - 1] = mapa
            bloque[:, tierra] = np.nan
            totales[v] += almacen.escribir_bloque(g, v, date(anio, 1, 1), bloque)
    return totales


# --------------------------------------------------------------------------- OPeNDAP (NASA)

def _granulos(**params) -> dict[str, str]:
    """{"AAAA-MM": URL OPeNDAP} de los ficheros de una colección (CMR da el nombre exacto de cada uno)."""
    urls, pagina = {}, 1
    while True:
        r = _get(f"{CMR}/granules.json", timeout=120, params={**params, "page_size": 2000, "page_num": pagina,
                                                               "sort_key": "start_date"})
        r.raise_for_status()
        entradas = r.json()["feed"]["entry"]
        for e in entradas:
            enlace = next((l["href"] for l in e.get("links", []) if l["href"].startswith(OPENDAP + "/collections")), None)
            if enlace:
                urls[e["time_start"][:7]] = enlace
        if len(entradas) < 2000:
            return urls
        pagina += 1


def _leer_ascii(texto: str, nombres: list[str]) -> dict[str, np.ndarray]:
    """
    Lee la salida .ascii de OPeNDAP: una línea por fila, «VAR.VAR[...][VAR.lat=30], v1, v2, ...».
    Los valores de relleno (1e15 en MERRA-2, -9999 en SMAP) pasan a NaN.
    """
    filas: dict[str, list[np.ndarray]] = {n: [] for n in nombres}
    for linea in texto.splitlines():
        cabeza, _, resto = linea.partition(",")
        nombre = cabeza.split(".")[0]
        if nombre in filas and "[" in cabeza:
            filas[nombre].append(np.array(resto.split(","), dtype=float))
    datos = {}
    for n, f in filas.items():
        if not f:
            raise ErrorDatos(f"OPeNDAP no devolvió {n}")
        a = np.vstack(f)
        a[(a > 1e14) | (a <= -9000)] = np.nan
        datos[n] = a
    return datos


def _opendap(url: str, expresion: str, nombres: list[str], token: str) -> dict[str, np.ndarray]:
    r = _get(f"{url}.ascii?{expresion}", timeout=300, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise ErrorDatos(f"OPeNDAP HTTP {r.status_code}: {r.text[:200]}")
    return _leer_ascii(r.text, nombres)


def _a_rejilla_merra(m: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Variables del proyecto en la rejilla mundial a partir de las de MERRA de un mes."""
    salida = {}
    for v, f in MERRA_VARIABLES.items():
        if f is not None:
            salida[v] = almacen.a_rejilla(f(m), _MERRA_LATS, _MERRA_LONS)
    # Dirección: media de los componentes en la celda y después el ángulo (de dónde sopla).
    u = almacen.a_rejilla(m["ULML"], _MERRA_LATS, _MERRA_LONS)
    w = almacen.a_rejilla(m["VLML"], _MERRA_LATS, _MERRA_LONS)
    salida["viento_direccion"] = ((270 - np.degrees(np.arctan2(w, u))) % 360).astype("float32")
    return salida


def marcar_mar_merra(g, token: str) -> None:
    """Máscara de mar: celdas con al menos un 50 % de océano según MERRA-2 (fichero constante M2C0NXASM)."""
    url = next(iter(_granulos(short_name="M2C0NXASM", version="5.12.4", provider="GES_DISC").values()))
    fraccion = _opendap(url, "FROCEAN" + _MERRA_RECORTE, ["FROCEAN"], token)["FROCEAN"]
    almacen.marcar_mar(g, almacen.a_rejilla(fraccion, _MERRA_LATS, _MERRA_LONS) >= 0.5)


def recolectar_merra(g, variables: list[str], forzar: date | None, token: str) -> dict[str, int]:
    """Todas las variables de MERRA-2 a la vez (tres peticiones por mes), sólo los meses pendientes."""
    marcar_mar_merra(g, token)
    granulos = {c: _granulos(short_name=c, version="5.12.4", provider="GES_DISC") for c in MERRA_COLECCIONES}
    ultimo = _mes(min(max(u) for u in granulos.values()))   # último mes presente en las tres colecciones
    meses = meses_pendientes(g, variables, almacen.ORIGEN, ultimo, forzar)
    log.info("MERRA-2: %d meses pendientes hasta %s, %d peticiones", len(meses), ultimo.isoformat()[:7], 3 * len(meses))

    def mes_completo(mes: date) -> dict[str, np.ndarray]:
        clave, datos = mes.isoformat()[:7], {}
        for coleccion, nombres in MERRA_COLECCIONES.items():
            expresion = ",".join(n + _MERRA_RECORTE for n in nombres)
            datos.update(_opendap(granulos[coleccion][clave], expresion, nombres, token))
        return _a_rejilla_merra(datos)

    totales = {v: 0 for v in variables}
    for anio in sorted({m.year for m in meses}):       # un año cada vez: memoria acotada
        resultados: dict[str, dict[date, np.ndarray]] = {v: {} for v in variables}
        with ThreadPoolExecutor(max_workers=HILOS) as pool:
            futuros = {pool.submit(mes_completo, m): m for m in meses if m.year == anio}
            for futuro in as_completed(futuros):
                mes = futuros[futuro]
                try:
                    mapas = futuro.result()
                except (ErrorDatos, requests.RequestException, KeyError, ValueError) as e:
                    log.warning("MERRA-2 %s: %s (se reintentará)", mes.isoformat()[:7], e)
                    continue
                for v in variables:
                    resultados[v][mes] = mapas[v]
        for v, n in _escribir_por_anios(g, resultados).items():
            totales[v] += n
        almacen.consolidar()
        log.info("MERRA-2 %d: %d meses guardados", anio, len(resultados[variables[0]]))
    return totales


def recolectar_smap(g, forzar: date | None, token: str) -> int:
    granulos = _granulos(collection_concept_id=SMAP["concepto"])
    ultimo = _mes(max(granulos))
    meses = [m for m in meses_pendientes(g, ["salinidad"], _mes(SMAP["desde"]), ultimo, forzar)
             if m.isoformat()[:7] in granulos]
    log.info("SMAP: %d meses pendientes hasta %s", len(meses), ultimo.isoformat()[:7])
    v = SMAP["variable"]

    def un_mes(mes: date) -> np.ndarray:
        # La variable entera: este servidor rechaza la restricción de rango completo [0:1:719][0:1:1439].
        datos = _opendap(granulos[mes.isoformat()[:7]], v, [v], token)[v]
        if datos.shape != (720, 1440):
            raise ErrorDatos(f"SMAP con forma inesperada {datos.shape}")
        return almacen.a_rejilla(datos, _SMAP_LATS, _SMAP_LONS)

    resultados: dict[date, np.ndarray] = {}
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        futuros = {pool.submit(un_mes, m): m for m in meses}
        for futuro in as_completed(futuros):
            mes = futuros[futuro]
            try:
                resultados[mes] = futuro.result()
            except (ErrorDatos, requests.RequestException, ValueError) as e:
                log.warning("SMAP %s: %s (se reintentará)", mes.isoformat()[:7], e)
    return _escribir_por_anios(g, {"salinidad": resultados})["salinidad"]


# --------------------------------------------------------------------------- ERDDAP (MODIS)

def _ultimo_mes_erddap(f: dict) -> date:
    r = _get(f"{f['servidor']}/griddap/{f['dataset']}.json?time[last]", timeout=TIMEOUT_ERDDAP)
    r.raise_for_status()
    return _mes(r.json()["table"]["rows"][0][0])


def _mes_erddap(f: dict, mes: date) -> np.ndarray:
    """Mapa mundial de un mes en NetCDF (binario, ~4 MB), promediado a la rejilla."""
    fin = date.fromordinal(almacen.sumar_meses(mes, 1).toordinal() - 1)
    url = (f"{f['servidor']}/griddap/{f['dataset']}.nc?{f['variable']}"
           f"[({mes}T00:00:00Z):1:({fin}T23:59:59Z)][0:{PASO_MODIS}:last][0:{PASO_MODIS}:last]")
    r = _get(url, timeout=600)
    if r.status_code == 404 and "no matching results" in r.text:
        return _bloque_vacio(1)[0]
    if r.status_code != 200:
        raise ErrorDatos(f"ERDDAP {f['dataset']} HTTP {r.status_code}: {r.text[:200]}")
    with xr.open_dataset(io.BytesIO(r.content), engine="scipy") as ds:
        datos = ds[f["variable"]].mean("time").values.astype(float)   # un compuesto por mes
        return almacen.a_rejilla(datos, ds["latitude"].values, ds["longitude"].values)


def recolectar_erddap(g, variable: str, forzar: date | None) -> int:
    total = 0
    for f in ERDDAP_FUENTES[variable]:
        fin = _mes(f["hasta"]) if f["hasta"] else min(_ultimo_mes_erddap(f), date.today().replace(day=1))
        meses = meses_pendientes(g, [variable], _mes(f["desde"]), fin, forzar)
        log.info("%-20s %d meses pendientes en %s", variable, len(meses), f["dataset"])
        # ERDDAP es más lento: dos peticiones a la vez y escritura cada año.
        for anio in sorted({m.year for m in meses}):
            resultados: dict[date, np.ndarray] = {}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futuros = {pool.submit(_mes_erddap, f, m): m for m in meses if m.year == anio}
                for futuro in as_completed(futuros):
                    mes = futuros[futuro]
                    try:
                        resultados[mes] = futuro.result()
                    except (ErrorDatos, requests.RequestException, ValueError, KeyError) as e:
                        log.warning("%s %s: %s (se reintentará)", variable, mes.isoformat()[:7], e)
            total += _escribir_por_anios(g, {variable: resultados})[variable]
            almacen.consolidar()
            log.info("%-20s %d guardado", variable, anio)
    return total


# --------------------------------------------------------------------------- principal

def recolectar(variables: list[str], desde: date | None = None) -> None:
    token = _token_earthdata(None)
    g = almacen.abrir_escritura(variables, {v: VARIABLES[v]["unidades"] for v in variables})
    forzar = desde.replace(day=1) if desde else None

    pasos = []
    merra = [v for v in variables if v in MERRA_VARIABLES]
    if merra:
        pasos.append(("MERRA-2", lambda: sum(recolectar_merra(g, merra, forzar, token).values()), True))
    if "salinidad" in variables:
        pasos.append(("salinidad", lambda: recolectar_smap(g, forzar, token), True))
    for v in [v for v in variables if v in ERDDAP_FUENTES]:
        pasos.append((v, lambda v=v: recolectar_erddap(g, v, forzar), False))

    for nombre, paso, necesita_token in pasos:
        t0 = time.monotonic()
        try:
            if necesita_token and not token:
                raise ErrorDatos("Falta EARTHDATA_TOKEN")
            log.info("%-20s OK: %d valores en %.0fs", nombre, paso(), time.monotonic() - t0)
        except Exception as e:  # una fuente que falla no detiene las demás
            log.error("%-20s FALLO: %s", nombre, e)
        finally:
            almacen.consolidar()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Recolector mensual mundial")
    parser.add_argument("--desde", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                        help="volver a pedir todos los meses desde esta fecha (AAAA-MM-DD)")
    parser.add_argument("--variables", nargs="+", choices=list(VARIABLES), default=VARIABLES_RECOLECTADAS)
    parser.add_argument("--sin-especies", action="store_true",
                        help="no recalcular las medias anuales ni las especies principales al terminar")
    args = parser.parse_args()
    recolectar(args.variables, args.desde)
    if not args.sin_especies:
        import especies
        especies.calcular_principales()
    import exportar_trends
    try:
        exportar_trends.exportar()      # datos reales para la pantalla Trends de la web
    except Exception as e:
        log.error("exportar_trends FALLO: %s", e)


if __name__ == "__main__":
    main()
