"""
Obtención de variables ambientales del Mediterráneo para una coordenada (lat, lon).

Fuentes:
  * NASA Giovanni in the Cloud - Time Series API
    https://api.giovanni.earthdata.nasa.gov/timeseries
    Requiere token de Earthdata Login (variable de entorno EARTHDATA_TOKEN).
    La lista de variables que admite está en
    https://api.giovanni.earthdata.nasa.gov/configured-variables/
  * NOAA CoastWatch ERDDAP (datos MODIS / SMAP de NASA redistribuidos, sin token).
    Se usa sólo para lo que la API de Giovanni no ofrece: salinidad, fluorescencia
    y clorofila posterior a 2015.

Uso rápido:
    export EARTHDATA_TOKEN="..."   # https://urs.earthdata.nasa.gov -> Generate Token
    python datos_mediterraneo.py 38.0 5.0 2025-06-01 2025-06-30
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

import requests

GIOVANNI_URL = "https://api.giovanni.earthdata.nasa.gov/timeseries"
GIOVANNI_QUOTA_URL = "https://api.giovanni.earthdata.nasa.gov/quota"
TIMEOUT_GIOVANNI = 120  # segundos
TIMEOUT_ERDDAP = 60

# Caché en disco de resultados correctos: los datos de fechas pasadas no cambian,
# así que una consulta repetida (mismo punto, variable y fechas) es instantánea.
# Se desactiva con DATOS_SIN_CACHE=1.
CACHE_DIR = Path(os.environ.get("DATOS_CACHE_DIR", Path(__file__).parent / ".cache_datos"))

# Límites aproximados del mar Mediterráneo (desde el Estrecho de Gibraltar; sin el Mar Negro).
MEDITERRANEO = {"lat_min": 30.0, "lat_max": 46.0, "lon_min": -6.0, "lon_max": 36.5}

# Catálogo de variables. Cada variable tiene una o más fuentes ordenadas por
# preferencia; se elige la primera cuya cobertura temporal incluye la fecha de inicio.
#   tipo "giovanni": `dataset` es el ID de variable de Giovanni.
#   tipo "erddap":   `servidor`, `dataset`, `variable` y `dims_extra` (dimensiones
#                    entre tiempo y latitud, p. ej. altitud).
VARIABLES = {
    "temperatura": {
        "nombre": "Temperatura de superficie (piel) - MERRA-2",
        "unidades": "°C",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXSLV_5_12_4_TS",
             "desde": "1980-01-01", "hasta": None,
             "convertir": lambda k: k - 273.15},  # K -> °C
        ],
    },
    "salinidad": {
        "nombre": "Salinidad superficial del mar - SMAP",
        "unidades": "PSU",
        "fuentes": [
            {"tipo": "erddap", "servidor": "https://coastwatch.noaa.gov/erddap",
             "dataset": "noaacwSMAPsssDaily", "variable": "sss", "dims_extra": ["(0.0)"],
             "desde": "2015-04-01", "hasta": None},
        ],
    },
    "fitoplancton": {
        "nombre": "Clorofila total (indicador de fitoplancton)",
        "unidades": "mg m-3",
        "fuentes": [
            # Modelo NOBM en Giovanni: sólo hasta 2015.
            {"tipo": "giovanni", "dataset": "NOBM_DAY_R2017_tot",
             "desde": "1998-01-01", "hasta": "2015-12-31"},
            # MODIS Aqua clorofila-a (8 días) para fechas recientes.
            {"tipo": "erddap", "servidor": "https://coastwatch.pfeg.noaa.gov/erddap",
             "dataset": "erdMH1chla8day_R2022NRT", "variable": "chlorophyll", "dims_extra": [],
             "desde": "2022-03-02", "hasta": None},
        ],
    },
    "fluorescencia": {
        "nombre": "Fluorescencia de clorofila (nFLH) - MODIS Aqua",
        "unidades": "W m-2 um-1 sr-1",
        "fuentes": [
            {"tipo": "erddap", "servidor": "https://coastwatch.pfeg.noaa.gov/erddap",
             "dataset": "erdMH1cflh8day_R2022NRT", "variable": "nflh", "dims_extra": [],
             "desde": "2022-02-26", "hasta": None},
        ],
    },
    "no2": {
        "nombre": "NO2 troposférico (columna) - OMI",
        "unidades": "moléculas cm-2",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "OMNO2d_003_ColumnAmountNO2TropCloudScreened",
             "desde": "2004-10-01", "hasta": None},
        ],
    },
    "co2": {
        "nombre": "CO2 en columna (XCO2) - OCO-2 GEOS",
        "unidades": "mol mol-1",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "OCO2_GEOS_L3CO2_DAY_10r_XCO2",
             "desde": "2015-01-01", "hasta": None},
        ],
    },
    "aerosoles": {
        "nombre": "Espesor óptico de aerosoles a 550 nm - MERRA-2",
        "unidades": "adimensional",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXAER_5_12_4_TOTEXTTAU",
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    "polvo_sahariano": {
        "nombre": "Espesor óptico del polvo mineral a 550 nm - MERRA-2",
        "unidades": "adimensional",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXAER_5_12_4_DUEXTTAU",
             "desde": "1980-01-01", "hasta": None},
        ],
    },

    # Intercambio océano-atmósfera (MERRA-2). La API de Giovanni no ofrece el
    # mensual M2TMNXFLX; se usa el horario M2T1NXFLX y se promedia por día.
    "evaporacion": {
        "nombre": "Evaporación desde la superficie - MERRA-2",
        "unidades": "mm día-1",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_EVAP",
             "desde": "1980-01-01", "hasta": None,
             "convertir": lambda e: e * 86400},  # kg m-2 s-1 -> mm/día
        ],
    },
    "precipitacion": {
        "nombre": "Precipitación (corregida con observaciones) - MERRA-2",
        "unidades": "mm día-1",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_PRECTOTCORR",
             "desde": "1980-01-01", "hasta": None,
             "convertir": lambda p: p * 86400},  # kg m-2 s-1 -> mm/día
        ],
    },
    "calor_latente": {
        "nombre": "Flujo de calor latente (positivo: el mar pierde calor) - MERRA-2",
        "unidades": "W m-2",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_EFLUX",
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    "calor_sensible": {
        "nombre": "Flujo de calor sensible (positivo: el mar pierde calor) - MERRA-2",
        "unidades": "W m-2",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_HFLUX",
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    "viento_velocidad": {
        "nombre": "Velocidad del viento en superficie - MERRA-2",
        "unidades": "m s-1",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_SPEED",
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    # Variables derivadas: se descargan varios datasets de Giovanni, se promedian por
    # día y `combinar` calcula el valor diario a partir de ellos.
    "viento_direccion": {
        "nombre": "Dirección del viento en el nivel más bajo (de dónde sopla) - MERRA-2",
        "unidades": "grados (0 = N, 90 = E)",
        "circular": True,
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXFLX_5_12_4_ULML + M2T1NXFLX_5_12_4_VLML",
             "componentes": {"u": "M2T1NXFLX_5_12_4_ULML", "v": "M2T1NXFLX_5_12_4_VLML"},
             # Con u y v medios del día sale la dirección del viento resultante.
             "combinar": lambda c: (270 - math.degrees(math.atan2(c["v"], c["u"]))) % 360,
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    "dif_temp_mar_aire": {
        "nombre": "Diferencia de temperatura mar-aire (superficie - aire a 2 m) - MERRA-2",
        "unidades": "°C",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "M2T1NXSLV_5_12_4_TS - M2T1NXSLV_5_12_4_T2M",
             "componentes": {"ts": "M2T1NXSLV_5_12_4_TS", "t2m": "M2T1NXSLV_5_12_4_T2M"},
             "combinar": lambda c: c["ts"] - c["t2m"],
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    "dif_humedad_mar_aire": {
        "nombre": "Diferencia de humedad específica mar-aire (qsat(TS) - q del nivel más bajo) - MERRA-2",
        "unidades": "g kg-1",
        "fuentes": [
            {"tipo": "giovanni", "dataset": "qsat(M2T1NXSLV_5_12_4_TS) - M2I1NXLFO_5_12_4_QLML",
             "componentes": {"ts": "M2T1NXSLV_5_12_4_TS", "q": "M2I1NXLFO_5_12_4_QLML"},
             "combinar": lambda c: (_humedad_saturacion_mar(c["ts"]) - c["q"]) * 1000,
             "desde": "1980-01-01", "hasta": None},
        ],
    },
    # Sólo en el recolector mensual (recolector.py); sin consulta diaria.
    "humedad_especifica": {
        "nombre": "Humedad específica del aire junto al mar (nivel más bajo) - MERRA-2",
        "unidades": "g kg-1",
        "fuentes": [],
    },
    "vapor_agua": {
        "nombre": "Vapor de agua total en la columna - MERRA-2",
        "unidades": "kg m-2",
        "fuentes": [],
    },
}

VARIABLES_POR_DEFECTO = ["temperatura", "salinidad", "fitoplancton", "fluorescencia", "no2", "aerosoles",
                         "evaporacion", "calor_latente", "calor_sensible", "viento_velocidad",
                         "viento_direccion", "dif_temp_mar_aire", "dif_humedad_mar_aire"]


def _humedad_saturacion_mar(t_kelvin: float, presion_hpa: float = 1013.25) -> float:
    """
    Humedad específica de saturación (kg/kg) sobre agua de mar a la temperatura dada.
    Fórmula de Bolton (1980) con el factor 0,98 por la salinidad. Se usa presión
    estándar: con la presión real el resultado cambia menos de un 2 %.
    """
    t = t_kelvin - 273.15
    e = 0.98 * 6.112 * math.exp(17.67 * t / (t + 243.5))  # hPa
    return 0.622 * e / (presion_hpa - 0.378 * e)


def media_serie(variable: str, valores: list[float]) -> float:
    """Media de una serie; las direcciones (grados) se promedian como vectores."""
    if VARIABLES[variable].get("circular"):
        s = sum(math.sin(math.radians(v)) for v in valores)
        c = sum(math.cos(math.radians(v)) for v in valores)
        return round(math.degrees(math.atan2(s, c)), 6) % 360
    return round(mean(valores), 6)


class ErrorDatos(Exception):
    """Error al obtener una variable (se informa por variable, no aborta el resto)."""


# --------------------------------------------------------------------------- utilidades

def _a_fecha(valor: str | date) -> date:
    if isinstance(valor, date):
        return valor
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _token_earthdata(token: str | None) -> str | None:
    if token:
        return token
    if os.environ.get("EARTHDATA_TOKEN"):
        return os.environ["EARTHDATA_TOKEN"]
    # Alternativa: credenciales en ~/.netrc mediante la librería earthaccess (opcional).
    try:
        import earthaccess
        earthaccess.login(strategy="netrc")
        return earthaccess.get_edl_token()["access_token"]
    except Exception:
        return None


def _elegir_fuente(variable: str, inicio: date) -> dict:
    for fuente in VARIABLES[variable]["fuentes"]:
        desde = _a_fecha(fuente["desde"])
        hasta = _a_fecha(fuente["hasta"]) if fuente["hasta"] else date.max
        if desde <= inicio <= hasta:
            return fuente
    rangos = ", ".join(f"{f['desde']}..{f['hasta'] or 'hoy'}" for f in VARIABLES[variable]["fuentes"])
    raise ErrorDatos(f"Sin datos para {inicio}. Cobertura disponible: {rangos}")


def _diario(serie: list[tuple[str, float]]) -> list[dict]:
    """Agrupa una serie (posiblemente horaria) en medias diarias."""
    por_dia: dict[str, list[float]] = {}
    for instante, valor in serie:
        por_dia.setdefault(instante[:10], []).append(valor)
    return [{"fecha": dia, "valor": round(mean(vals), 6)} for dia, vals in sorted(por_dia.items())]


# --------------------------------------------------------------------------- fuentes

_memo_giovanni: dict[tuple, Future] = {}
_memo_cerrojo = threading.Lock()


def _consultar_giovanni(fuente: dict, lat: float, lon: float, inicio: date, fin: date,
                        token: str | None) -> list[tuple[str, float]]:
    """Serie de una fuente de Giovanni, simple o derivada de varios datasets."""
    if "componentes" not in fuente:
        return _serie_giovanni(fuente["dataset"], lat, lon, inicio, fin, token)

    # Derivada: media diaria de cada componente y combinación de los días comunes.
    diarias = {nombre: {d["fecha"]: d["valor"] for d in _diario(_serie_giovanni(ds, lat, lon, inicio, fin, token))}
               for nombre, ds in fuente["componentes"].items()}
    dias = sorted(set.intersection(*(set(d) for d in diarias.values())))
    return [(f"{dia}T12:00:00", fuente["combinar"]({n: d[dia] for n, d in diarias.items()})) for dia in dias]


def _serie_giovanni(dataset: str, lat: float, lon: float, inicio: date, fin: date,
                    token: str | None) -> list[tuple[str, float]]:
    """
    Descarga un dataset una sola vez por proceso: varias variables comparten
    datasets (TS se usa en tres) y se piden a la vez desde distintos hilos.
    """
    clave = (dataset, round(lat, 4), round(lon, 4), inicio, fin)
    with _memo_cerrojo:
        futuro = _memo_giovanni.get(clave)
        propio = futuro is None
        if propio:
            if len(_memo_giovanni) > 5000:
                _memo_giovanni.clear()
            futuro = _memo_giovanni[clave] = Future()
    if propio:
        try:
            futuro.set_result(_descargar_giovanni(dataset, lat, lon, inicio, fin, token))
        except Exception as e:
            with _memo_cerrojo:
                _memo_giovanni.pop(clave, None)  # un error no se memoriza
            futuro.set_exception(e)
    return futuro.result()


def _descargar_giovanni(dataset: str, lat: float, lon: float, inicio: date, fin: date,
                        token: str | None) -> list[tuple[str, float]]:
    if not token:
        raise ErrorDatos("Falta token de Earthdata (define EARTHDATA_TOKEN)")
    params = {
        "data": dataset,
        "location": f"[{lat:.4f},{lon:.4f}]",
        "time": f"{inicio}T00:00:00/{fin}T23:59:59",
    }
    r = requests.get(GIOVANNI_URL, params=params, timeout=TIMEOUT_GIOVANNI,
                     headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise ErrorDatos(f"Giovanni HTTP {r.status_code}: {r.text[:300]}")
    return _parsear_csv_giovanni(r.text)


def _parsear_csv_giovanni(texto: str) -> list[tuple[str, float]]:
    """
    El CSV de Giovanni empieza con líneas de metadatos "clave,valor"
    (param_name, unit, lat, lon, fill_value...) seguidas de la cabecera
    "Timestamp (UTC),<variable>" y las filas de datos.
    """
    metadatos: dict[str, str] = {}
    serie: list[tuple[str, float]] = []
    for linea in texto.splitlines():
        partes = [p.strip() for p in linea.split(",")]
        if len(partes) < 2 or not partes[0]:
            continue
        try:
            instante = datetime.fromisoformat(partes[0].replace("Z", "")).isoformat()
            valor = float(partes[1])
        except ValueError:
            if not serie:
                metadatos[partes[0]] = partes[1]
            continue
        serie.append((instante, valor))

    relleno = None
    for clave in ("fill_value", "_FillValue", "missing_value"):
        if clave in metadatos:
            try:
                relleno = float(metadatos[clave])
            except ValueError:
                pass
    if not serie and metadatos:
        raise ErrorDatos("Giovanni no devolvió datos (punto fuera del dataset o sin cobertura)")
    return [(t, v) for t, v in serie
            if not math.isnan(v) and (relleno is None or not math.isclose(v, relleno))]


def _consultar_erddap(fuente: dict, lat: float, lon: float, inicio: date, fin: date) -> list[tuple[str, float]]:
    dims = [f"({inicio}T00:00:00Z):1:({fin}T23:59:59Z)", *fuente["dims_extra"],
            f"({lat})", f"({lon})"]
    consulta = fuente["variable"] + "".join(f"[{d}]" for d in dims)
    url = f"{fuente['servidor']}/griddap/{fuente['dataset']}.json?{consulta}"
    r = requests.get(url, timeout=TIMEOUT_ERDDAP)
    if r.status_code == 404 and "no matching results" in r.text:
        return []
    if r.status_code != 200:
        raise ErrorDatos(f"ERDDAP HTTP {r.status_code}: {r.text[:300]}")
    filas = r.json()["table"]["rows"]
    return [(fila[0].replace("Z", ""), fila[-1]) for fila in filas if fila[-1] is not None]


# --------------------------------------------------------------------------- API pública

def _ruta_cache(variable: str, lat: float, lon: float, inicio: date, fin: date) -> Path:
    clave = f"{variable}|{lat:.4f}|{lon:.4f}|{inicio}|{fin}"
    return CACHE_DIR / f"{hashlib.sha1(clave.encode()).hexdigest()}.json"


def obtener_variable(variable: str, lat: float, lon: float, fecha_inicio: str | date,
                     fecha_fin: str | date, token: str | None = None) -> dict:
    """Obtiene la serie diaria de una variable del catálogo `VARIABLES` en un punto."""
    if variable not in VARIABLES:
        raise ValueError(f"Variable desconocida '{variable}'. Disponibles: {list(VARIABLES)}")
    inicio, fin = _a_fecha(fecha_inicio), _a_fecha(fecha_fin)

    usar_cache = os.environ.get("DATOS_SIN_CACHE") != "1"
    ruta = _ruta_cache(variable, lat, lon, inicio, fin)
    if usar_cache and ruta.exists():
        resultado = json.loads(ruta.read_text())
        resultado.update(segundos=0.0, cache=True)
        return resultado

    t0 = time.monotonic()
    info = VARIABLES[variable]
    resultado = {"nombre": info["nombre"], "unidades": info["unidades"],
                 "fuente": None, "dataset": None, "serie": [], "media": None,
                 "ultimo": None, "error": None}
    try:
        fuente = _elegir_fuente(variable, inicio)
        resultado["fuente"] = "NASA Giovanni" if fuente["tipo"] == "giovanni" else "NOAA CoastWatch ERDDAP"
        resultado["dataset"] = fuente["dataset"]
        if fuente["tipo"] == "giovanni":
            serie = _consultar_giovanni(fuente, lat, lon, inicio, fin, _token_earthdata(token))
        else:
            serie = _consultar_erddap(fuente, lat, lon, inicio, fin)
        convertir = fuente.get("convertir")
        if convertir:
            serie = [(t, convertir(v)) for t, v in serie]
        resultado["serie"] = _diario(serie)
        if resultado["serie"]:
            resultado["media"] = media_serie(variable, [p["valor"] for p in resultado["serie"]])
            resultado["ultimo"] = resultado["serie"][-1]
    except (ErrorDatos, requests.RequestException, ValueError, KeyError) as e:
        resultado["error"] = str(e)
    resultado["segundos"] = round(time.monotonic() - t0, 1)
    resultado["cache"] = False

    # Sólo se guardan resultados sin error y de periodos ya cerrados (fin anterior a hoy).
    if usar_cache and not resultado["error"] and fin < date.today():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(resultado, ensure_ascii=False))
    return resultado


def obtener_datos_mediterraneo(lat: float, lon: float,
                               fecha_inicio: str | date | None = None,
                               fecha_fin: str | date | None = None,
                               variables: list[str] | None = None,
                               token: str | None = None,
                               solo_mediterraneo: bool = True) -> dict:
    """
    Devuelve un diccionario serializable a JSON con las series diarias de
    las variables de `VARIABLES_POR_DEFECTO` (o las indicadas en `variables`)
    para la coordenada dada.

    Si no se indican fechas se usan los 30 días que terminan hace 60 días
    (MERRA-2 y OMI se publican con varias semanas de retraso).
    Cada variable lleva su propio campo `error`: un fallo en una fuente no
    impide obtener las demás.
    """
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordenadas fuera de rango")
    m = MEDITERRANEO
    if solo_mediterraneo and not (m["lat_min"] <= lat <= m["lat_max"] and m["lon_min"] <= lon <= m["lon_max"]):
        raise ValueError(f"({lat}, {lon}) está fuera del Mediterráneo {m}")

    fin = _a_fecha(fecha_fin) if fecha_fin else date.today() - timedelta(days=60)
    inicio = _a_fecha(fecha_inicio) if fecha_inicio else fin - timedelta(days=29)
    if inicio > fin:
        raise ValueError("fecha_inicio es posterior a fecha_fin")
    variables = variables or VARIABLES_POR_DEFECTO
    token = _token_earthdata(token)

    # Las peticiones son independientes y lentas: se lanzan en paralelo.
    # Máximo 6 a la vez para no saturar Giovanni (una variable derivada hace varias peticiones).
    with ThreadPoolExecutor(max_workers=min(6, len(variables))) as pool:
        futuros = {v: pool.submit(obtener_variable, v, lat, lon, inicio, fin, token) for v in variables}
        datos = {v: f.result() for v, f in futuros.items()}

    return {"lat": lat, "lon": lon,
            "fecha_inicio": inicio.isoformat(), "fecha_fin": fin.isoformat(),
            "variables": datos}


# --------------------------------------------------------------------------- prueba

def diagnostico_giovanni(token: str | None = None) -> None:
    """Prueba la API de Giovanni con peticiones mínimas para aislar la causa de un fallo."""
    token = _token_earthdata(token)
    if not token:
        print("Sin token: define EARTHDATA_TOKEN")
        return
    cabeceras = {"Authorization": f"Bearer {token}"}

    # Revisión del token sin mostrarlo: un token de Earthdata es un JWT "xxx.yyy.zzz".
    print(f"token            {len(token)} caracteres, empieza por {token[:3]!r}, "
          f"{token.count('.')} puntos, espacios/comillas: {any(c in token for c in ' \"' + chr(39))}")
    try:
        import base64
        carga = token.split(".")[1]
        datos = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
        caduca = datetime.fromtimestamp(datos["exp"])
        print(f"                 emisor={datos.get('iss')} caduca={caduca:%Y-%m-%d %H:%M} "
              f"{'(CADUCADO)' if caduca < datetime.now() else '(vigente)'}")
    except Exception as e:
        print(f"                 no se puede leer como JWT: {e}")

    # CMR (buscador de la NASA) valida el mismo token de Earthdata: si aquí funciona,
    # el token es bueno y el rechazo es sólo de Giovanni.
    t0 = time.monotonic()
    r = requests.get("https://cmr.earthdata.nasa.gov/search/collections.json",
                     params={"short_name": "M2T1NXSLV", "page_size": 1}, headers=cabeceras, timeout=60)
    print(f"Earthdata (CMR)  HTTP {r.status_code} {time.monotonic() - t0:5.1f}s  "
          f"{'token aceptado' if r.status_code == 200 else r.text[:200]}")

    t0 = time.monotonic()
    r = requests.get(GIOVANNI_QUOTA_URL, headers=cabeceras, timeout=60)
    print(f"cuota            HTTP {r.status_code} {time.monotonic() - t0:5.1f}s  {r.text[:200]}")

    pruebas = [
        # Ejemplo literal del tutorial oficial de la NASA (sirve de referencia).
        ("tutorial GLDAS", "GLDAS_NOAH025_3H_2_1_Tair_f_inst", "[45.125,-98.125]",
         "2000-01-01T03:00:00/2000-01-02T21:00:00"),
        ("TS 1 día", "M2T1NXSLV_5_12_4_TS", "[38.0000,5.0000]",
         "2025-06-01T00:00:00/2025-06-01T23:00:00"),
        ("TS 1 día 23:59", "M2T1NXSLV_5_12_4_TS", "[38.0000,5.0000]",
         "2025-06-01T00:00:00/2025-06-01T23:59:59"),
        ("TS 30 días", "M2T1NXSLV_5_12_4_TS", "[38.0000,5.0000]",
         "2025-06-01T00:00:00/2025-06-30T23:00:00"),
        ("AOD 1 día", "M2T1NXAER_5_12_4_TOTEXTTAU", "[38.0000,5.0000]",
         "2025-06-01T00:00:00/2025-06-01T23:00:00"),
        ("NO2 3 días", "OMNO2d_003_ColumnAmountNO2TropCloudScreened", "[38.0000,5.0000]",
         "2025-06-01T00:00:00/2025-06-03T00:00:00"),
    ]
    for nombre, dataset, punto, periodo in pruebas:
        t0 = time.monotonic()
        try:
            r = requests.get(GIOVANNI_URL, headers=cabeceras, timeout=TIMEOUT_GIOVANNI,
                             params={"data": dataset, "location": punto, "time": periodo})
            filas = len(r.text.splitlines())
            print(f"{nombre:16} HTTP {r.status_code} {time.monotonic() - t0:5.1f}s  "
                  f"{filas} líneas  {r.text[:150]!r}")
        except requests.RequestException as e:
            print(f"{nombre:16} EXCEPCIÓN {time.monotonic() - t0:5.1f}s  {e}")


def main() -> None:
    # python datos_mediterraneo.py [lat lon [fecha_inicio fecha_fin]]
    # python datos_mediterraneo.py --diagnostico
    args = sys.argv[1:]
    if args[:1] == ["--diagnostico"]:
        diagnostico_giovanni()
        return
    lat, lon = (float(args[0]), float(args[1])) if len(args) >= 2 else (38.0, 5.0)  # mar Balear
    inicio, fin = (args[2], args[3]) if len(args) >= 4 else ("2025-06-01", "2025-06-30")

    if not _token_earthdata(None):
        print("AVISO: sin EARTHDATA_TOKEN; las variables de Giovanni devolverán error.\n", file=sys.stderr)

    print(f"Consultando ({lat}, {lon}) del {inicio} al {fin}...\n", file=sys.stderr)
    datos = obtener_datos_mediterraneo(lat, lon, inicio, fin)

    for clave, v in datos["variables"].items():
        if v["error"]:
            estado = f"ERROR: {v['error']}"
        elif not v["serie"]:
            estado = "sin datos válidos (nubes, tierra o huecos del satélite)"
        else:
            estado = f"media={v['media']:.4g} {v['unidades']}  ({len(v['serie'])} días)"
        tiempo = "caché" if v.get("cache") else f"{v.get('segundos', 0):.1f}s"
        print(f"{clave:21} {tiempo:>6} [{v['dataset']}] {estado}", file=sys.stderr)

    print(json.dumps(datos, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
