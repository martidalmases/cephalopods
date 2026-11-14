"""
Recolector periódico: descarga las variables para toda la rejilla del Mediterráneo
y las guarda en el almacén Zarr (almacen.py). Pensado para ejecutarse cada día con
un temporizador de systemd o cron; sólo descarga lo que falta.

  * Variables de ERDDAP (salinidad, clorofila, fluorescencia): una petición por
    variable y mes cubre todo el Mediterráneo con una rejilla de 0,5°. Las celdas
    con datos de satélite forman la máscara de mar.
  * Variables de Giovanni (temperatura, NO2, aerosoles, flujos y viento de MERRA-2): la API sólo admite series
    en un punto, así que se consulta una rejilla más gruesa (1°, ~250 puntos).
    MERRA-2 y OMI se publican con retraso; el recolector pregunta a CMR cuál es
    el último día disponible y no pide más allá.

Uso:
    python recolector.py                      # actualización incremental (todas)
    python recolector.py --desde 2025-01-01   # primera carga / rellenar huecos
    python recolector.py --variables salinidad fluorescencia
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import numpy as np
import requests

import almacen
from datos_mediterraneo import (TIMEOUT_ERDDAP, VARIABLES, VARIABLES_POR_DEFECTO, ErrorDatos, _a_fecha,
                                _consultar_giovanni, _diario, _elegir_fuente, _token_earthdata)

log = logging.getLogger("recolector")

VARIABLES_RECOLECTADAS = VARIABLES_POR_DEFECTO
DIAS_HISTORICO = 365           # primera carga si no hay datos y no se indica --desde
SOLAPE_DIAS = 16               # se vuelven a pedir los últimos días (compuestos de 8 días, reprocesados NRT)
TROZO_DIAS = 31                # días por petición de ERDDAP (~5 MB de JSON)
HILOS_GIOVANNI = 4             # peticiones simultáneas a Giovanni (no saturar la cuota)

# Recorte del Mediterráneo para las peticiones de rejilla de ERDDAP.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 30.0, 46.0, -6.0, 36.5

# Salto de índices para obtener ~0,5° y orden del eje de latitud de cada dataset.
REJILLA_ERDDAP = {
    "noaacwSMAPsssDaily":      {"paso": 2,  "lat_descendente": False},  # 0,25°
    "erdMH1chla8day_R2022NRT": {"paso": 12, "lat_descendente": True},   # 4 km
    "erdMH1cflh8day_R2022NRT": {"paso": 12, "lat_descendente": True},
}

# Colección de CMR de cada variable de Giovanni (para saber el último día publicado).
# Los IDs de MERRA-2 ("M2T1NXFLX_5_12_4_EVAP") se deducen solos; el resto, aquí.
COLECCION_CMR = {
    "OMNO2d_003_ColumnAmountNO2TropCloudScreened": ("OMNO2d", "003"),
    "OCO2_GEOS_L3CO2_DAY_10r_XCO2": ("OCO2_GEOS_L3CO2_DAY", "10r"),
    "NOBM_DAY_R2017_tot": ("NOBM_DAY", "R2017"),
}


def _coleccion_cmr(dataset: str) -> tuple[str, str] | None:
    if dataset in COLECCION_CMR:
        return COLECCION_CMR[dataset]
    partes = dataset.split("_")
    if dataset.startswith("M2") and len(partes) > 4:
        return partes[0], ".".join(partes[1:4])
    return None


def _get(url: str, intentos: int = 3, **kwargs) -> requests.Response:
    """GET con reintentos ante errores temporales (ERDDAP devuelve 503 a veces)."""
    for i in range(intentos):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code not in (502, 503, 504):
                return r
        except requests.RequestException:
            if i == intentos - 1:
                raise
        time.sleep(10 * (i + 1))
    return r


# --------------------------------------------------------------------------- ERDDAP

def _ultima_fecha_erddap(fuente: dict) -> date:
    r = _get(f"{fuente['servidor']}/griddap/{fuente['dataset']}.json?time[last]", timeout=TIMEOUT_ERDDAP)
    r.raise_for_status()
    return _a_fecha(r.json()["table"]["rows"][0][0][:10])


def recolectar_erddap(g, variable: str, fuente: dict, desde: date, hasta: date) -> int:
    rejilla = REJILLA_ERDDAP[fuente["dataset"]]
    paso = rejilla["paso"]
    lat = f"({LAT_MAX}):{paso}:({LAT_MIN})" if rejilla["lat_descendente"] else f"({LAT_MIN}):{paso}:({LAT_MAX})"
    lon = f"({LON_MIN}):{paso}:({LON_MAX})"
    convertir = fuente.get("convertir")
    total = 0

    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + timedelta(days=TROZO_DIAS - 1), hasta)
        dims = [f"({inicio}T00:00:00Z):1:({fin}T23:59:59Z)", *fuente["dims_extra"], lat, lon]
        url = (f"{fuente['servidor']}/griddap/{fuente['dataset']}.json?"
               + fuente["variable"] + "".join(f"[{d}]" for d in dims))
        r = _get(url, timeout=300)
        if r.status_code == 404 and "no matching results" in r.text:
            filas = []  # trozo sin pasadas del satélite
        elif r.status_code != 200:
            raise ErrorDatos(f"ERDDAP HTTP {r.status_code}: {r.text[:300]}")
        else:
            filas = r.json()["table"]["rows"]

        # Bloque (días, lat, lon) del trozo; cada píxel de ERDDAP cae en su celda de 0,5°.
        bloque = np.full(((fin - inicio).days + 1, len(almacen.LATS), len(almacen.LONS)), np.nan, dtype="float32")
        for fila in filas:
            instante, la, lo, valor = fila[0], fila[-3], fila[-2], fila[-1]
            celda = almacen.indice_celda(la, lo)
            dia = (_a_fecha(instante[:10]) - inicio).days
            if valor is None or celda is None or not 0 <= dia < len(bloque):
                continue
            if almacen.MEDITERRANEO[celda]:
                bloque[(dia, *celda)] = convertir(valor) if convertir else valor
        n = almacen.escribir_bloque(g, variable, inicio, bloque)
        almacen.marcar_mar(g, ~np.isnan(bloque).all(axis=0))
        total += n
        log.info("%-20s %s..%s  %6d valores", variable, inicio, fin, n)
        inicio = fin + timedelta(days=1)
    return total


# --------------------------------------------------------------------------- Giovanni

def _ultima_fecha_cmr(fuente: dict) -> date | None:
    """Último día publicado; en una variable derivada, el del componente más atrasado."""
    fechas = []
    for dataset in fuente.get("componentes", {"_": fuente["dataset"]}).values():
        coleccion = _coleccion_cmr(dataset)
        if coleccion is None:
            continue
        nombre, version = coleccion
        r = _get("https://cmr.earthdata.nasa.gov/search/granules.json", timeout=60,
                 params={"short_name": nombre, "version": version, "provider": "GES_DISC",
                         "sort_key": "-start_date", "page_size": 1})
        entradas = r.json().get("feed", {}).get("entry", [])
        if entradas:
            fechas.append(_a_fecha(entradas[0]["time_end"][:10]))
    return min(fechas) if fechas else None


def recolectar_giovanni(g, variable: str, fuente: dict, desde: date, hasta: date, token: str) -> int:
    """
    Una petición por celda de 1°. Cada celda empieza el día siguiente a su último dato
    dentro de [desde, hasta], así que las que fallaron en otra ejecución se rellenan.
    """
    ii, jj = np.nonzero(g["mar"][:] & almacen.CELDAS_1_GRADO)
    if not len(ii):
        raise ErrorDatos("No hay celdas de mar: ejecuta antes una variable de ERDDAP (p. ej. salinidad)")
    if desde > hasta:
        return 0

    t0, t1 = almacen.indice_fecha(desde), almacen.indice_fecha(hasta) + 1
    bloque = g[variable][t0:t1]                     # (días, lat, lon) ya guardado
    tareas = []
    for i, j in zip(ii, jj):
        validos = np.flatnonzero(~np.isnan(bloque[:, i, j]))
        inicio = desde + timedelta(days=int(validos[-1]) + 1) if len(validos) else desde
        if inicio <= hasta:
            tareas.append((int(i), int(j), inicio))
    log.info("%-20s %d celdas por consultar en Giovanni (%s..%s)", variable, len(tareas), desde, hasta)

    nuevo = np.full_like(bloque, np.nan)
    convertir = fuente.get("convertir")
    errores = 0
    with ThreadPoolExecutor(max_workers=HILOS_GIOVANNI) as pool:
        futuros = {pool.submit(_consultar_giovanni, fuente, float(almacen.LATS[i]), float(almacen.LONS[j]),
                               inicio, hasta, token): (i, j) for i, j, inicio in tareas}
        for n, futuro in enumerate(as_completed(futuros), 1):
            i, j = futuros[futuro]
            try:
                serie = futuro.result()
            except (ErrorDatos, requests.RequestException) as e:
                errores += 1
                log.warning("%-20s (%s, %s): %s", variable, almacen.LATS[i], almacen.LONS[j], e)
                continue
            if convertir:
                serie = [(t, convertir(v)) for t, v in serie]
            for d in _diario(serie):
                dia = (_a_fecha(d["fecha"]) - desde).days
                if 0 <= dia < len(nuevo):
                    nuevo[dia, i, j] = d["valor"]
            if n % 25 == 0:
                log.info("%-20s %d/%d celdas", variable, n, len(tareas))
    if errores:
        log.warning("%-20s %d celdas con error (se reintentarán en la próxima ejecución)", variable, errores)
    return almacen.escribir_bloque(g, variable, desde, nuevo)


# --------------------------------------------------------------------------- principal

def recolectar(variables: list[str], desde: date | None = None) -> None:
    hoy = date.today()
    token = _token_earthdata(None)
    g = almacen.abrir_escritura(variables, {v: VARIABLES[v]["unidades"] for v in variables})
    # Primero las de ERDDAP: marcan las celdas de mar que luego consulta Giovanni.
    variables = sorted(variables, key=lambda v: _elegir_fuente(v, hoy - timedelta(days=30))["tipo"] != "erddap")

    for variable in variables:
        t0 = time.monotonic()
        try:
            fuente = _elegir_fuente(variable, hoy - timedelta(days=30))
            atributos = g[variable].attrs
            if desde:
                inicio = desde
            elif fuente["tipo"] == "erddap":
                # Incremental: unos días antes del último dato (compuestos de 8 días, reprocesados).
                inicio = (_a_fecha(atributos["hasta"]) - timedelta(days=SOLAPE_DIAS)
                          if atributos.get("hasta") else hoy - timedelta(days=DIAS_HISTORICO))
            else:
                # Giovanni revisa todo el histórico guardado: cada celda sigue donde se quedó.
                inicio = _a_fecha(atributos["desde"]) if atributos.get("desde") else hoy - timedelta(days=DIAS_HISTORICO)
            # Si empieza antes de la cobertura de la fuente (o del almacén), se recorta.
            inicio = max(inicio, _a_fecha(fuente["desde"]), almacen.ORIGEN)

            if fuente["tipo"] == "erddap":
                hasta = min(hoy, _ultima_fecha_erddap(fuente))
                n = recolectar_erddap(g, variable, fuente, inicio, hasta)
            else:
                if not token:
                    raise ErrorDatos("Falta EARTHDATA_TOKEN")
                hasta = min(hoy, _ultima_fecha_cmr(fuente) or hoy)
                n = recolectar_giovanni(g, variable, fuente, inicio, hasta, token)
            log.info("%-20s OK: %d valores en %.0fs", variable, n, time.monotonic() - t0)
        except Exception as e:  # una variable que falla no detiene las demás
            log.error("%-20s FALLO: %s", variable, e)
        finally:
            almacen.consolidar()  # los lectores (API) ven los datos nuevos


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Recolector de datos del Mediterráneo")
    parser.add_argument("--desde", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                        help="fecha inicial (AAAA-MM-DD); por defecto, incremental")
    parser.add_argument("--variables", nargs="+", choices=list(VARIABLES), default=VARIABLES_RECOLECTADAS)
    parser.add_argument("--sin-especies", action="store_true",
                        help="no recalcular la climatología ni las especies principales al terminar")
    args = parser.parse_args()
    recolectar(args.variables, args.desde)
    if not args.sin_especies:
        import especies
        especies.calcular_principales()


if __name__ == "__main__":
    main()
