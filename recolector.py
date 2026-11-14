"""
Recolector periódico: descarga las variables para toda la rejilla del Mediterráneo
y las guarda en la base de datos (bd.py). Pensado para ejecutarse cada día con
un temporizador de systemd o cron; sólo descarga lo que falta.

  * Variables de ERDDAP (salinidad, clorofila, fluorescencia): una petición por
    variable y mes cubre todo el Mediterráneo con una rejilla de 0,5°.
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

import requests

import bd
from datos_mediterraneo import (TIMEOUT_ERDDAP, VARIABLES, VARIABLES_POR_DEFECTO, ErrorDatos, _a_fecha,
                                _consultar_giovanni, _diario, _elegir_fuente, _token_earthdata)

log = logging.getLogger("recolector")

VARIABLES_RECOLECTADAS = VARIABLES_POR_DEFECTO
DIAS_HISTORICO = 365           # primera carga si la BD está vacía y no se indica --desde
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


def recolectar_erddap(con, variable: str, fuente: dict, desde: date, hasta: date) -> int:
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

        lote = []
        for fila in filas:
            instante, la, lo, valor = fila[0], fila[-3], fila[-2], fila[-1]
            if valor is None:
                continue
            la, lo = bd.ajustar_a_rejilla(la, lo)
            if not bd.es_mediterraneo(la, lo):
                continue
            lote.append((la, lo, instante[:10], convertir(valor) if convertir else valor))
        _guardar(con, variable, lote)
        total += len(lote)
        log.info("%-20s %s..%s  %6d valores", variable, inicio, fin, len(lote))
        inicio = fin + timedelta(days=1)
    return total


def _guardar(con, variable: str, lote: list[tuple[float, float, str, float]]) -> None:
    with con:
        for la, lo, fecha, valor in lote:
            # Los puntos de mar se crean al recibir su primer dato; los de lat/lon enteras
            # forman la rejilla de 1° que se consulta en Giovanni.
            con.execute("INSERT OR IGNORE INTO punto (lat, lon, giovanni) VALUES (?, ?, ?)",
                        (la, lo, int(la == int(la) and lo == int(lo))))
            con.execute(
                "INSERT OR REPLACE INTO medicion (punto_id, variable, fecha, valor) "
                "SELECT id, ?, ?, ? FROM punto WHERE lat = ? AND lon = ?",
                (variable, fecha, round(valor, 6), la, lo))


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


def recolectar_giovanni(con, variable: str, fuente: dict, desde: date, hasta: date,
                        forzar_desde: bool, token: str) -> int:
    puntos = con.execute("SELECT id, lat, lon FROM punto WHERE giovanni = 1").fetchall()
    if not puntos:
        raise ErrorDatos("No hay puntos en la BD: ejecuta antes una variable de ERDDAP (p. ej. salinidad)")

    # Rango por punto: desde el día siguiente al último guardado. Un punto sin datos
    # (falló antes) empieza donde empieza el histórico del resto de puntos.
    primera = con.execute("SELECT MIN(fecha) FROM medicion WHERE variable = ?", (variable,)).fetchone()[0]
    tareas = []
    for p in puntos:
        inicio = desde
        if not forzar_desde:
            ultima = con.execute("SELECT MAX(fecha) FROM medicion WHERE punto_id = ? AND variable = ?",
                                 (p["id"], variable)).fetchone()[0]
            if ultima:
                inicio = max(desde, _a_fecha(ultima) + timedelta(days=1))
            elif primera:
                inicio = min(desde, _a_fecha(primera))
        if inicio <= hasta:
            tareas.append((p, inicio))
    log.info("%-20s %d puntos por consultar en Giovanni (hasta %s)", variable, len(tareas), hasta)

    convertir = fuente.get("convertir")
    total = errores = 0
    with ThreadPoolExecutor(max_workers=HILOS_GIOVANNI) as pool:
        futuros = {pool.submit(_consultar_giovanni, fuente, p["lat"], p["lon"], inicio, hasta, token): p
                   for p, inicio in tareas}
        for n, futuro in enumerate(as_completed(futuros), 1):
            p = futuros[futuro]
            try:
                serie = futuro.result()
            except (ErrorDatos, requests.RequestException) as e:
                errores += 1
                log.warning("%-20s (%s, %s): %s", variable, p["lat"], p["lon"], e)
                continue
            if convertir:
                serie = [(t, convertir(v)) for t, v in serie]
            diaria = _diario(serie)
            with con:
                con.executemany(
                    "INSERT OR REPLACE INTO medicion (punto_id, variable, fecha, valor) VALUES (?, ?, ?, ?)",
                    [(p["id"], variable, d["fecha"], d["valor"]) for d in diaria])
            total += len(diaria)
            if n % 25 == 0:
                log.info("%-20s %d/%d puntos", variable, n, len(tareas))
    if errores:
        log.warning("%-20s %d puntos con error (se reintentarán en la próxima ejecución)", variable, errores)
    return total


# --------------------------------------------------------------------------- principal

def recolectar(variables: list[str], desde: date | None = None) -> None:
    con = bd.conectar()
    hoy = date.today()
    token = _token_earthdata(None)
    # Primero las de ERDDAP: crean los puntos de mar que luego usa Giovanni.
    variables = sorted(variables, key=lambda v: _elegir_fuente(v, hoy - timedelta(days=30))["tipo"] != "erddap")

    for variable in variables:
        t0 = time.monotonic()
        try:
            if desde:
                inicio = desde
            else:
                ultima = con.execute("SELECT MAX(fecha) FROM medicion WHERE variable = ?", (variable,)).fetchone()[0]
                inicio = (_a_fecha(ultima) - timedelta(days=SOLAPE_DIAS)) if ultima else hoy - timedelta(days=DIAS_HISTORICO)

            # Fuente vigente para el periodo; si empieza antes de su cobertura, se recorta.
            fuente = _elegir_fuente(variable, hoy - timedelta(days=30))
            inicio = max(inicio, _a_fecha(fuente["desde"]))

            if fuente["tipo"] == "erddap":
                hasta = min(hoy, _ultima_fecha_erddap(fuente))
                n = recolectar_erddap(con, variable, fuente, inicio, hasta)
            else:
                if not token:
                    raise ErrorDatos("Falta EARTHDATA_TOKEN")
                hasta = min(hoy, _ultima_fecha_cmr(fuente) or hoy)
                n = recolectar_giovanni(con, variable, fuente, inicio, hasta, desde is not None, token)
            log.info("%-20s OK: %d valores en %.0fs", variable, n, time.monotonic() - t0)
        except Exception as e:  # una variable que falla no detiene las demás
            log.error("%-20s FALLO: %s", variable, e)
    con.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Recolector de datos del Mediterráneo")
    parser.add_argument("--desde", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                        help="fecha inicial (AAAA-MM-DD); por defecto, incremental")
    parser.add_argument("--variables", nargs="+", choices=list(VARIABLES), default=VARIABLES_RECOLECTADAS)
    args = parser.parse_args()
    recolectar(args.variables, args.desde)


if __name__ == "__main__":
    main()
