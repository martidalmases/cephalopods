"""
Relación entre las especies marinas y las condiciones ambientales del Mediterráneo.

Sólo usa las variables ambientales ya guardadas por el recolector. De fuera se
obtienen únicamente los avistamientos de cada especie (OBIS, sin registro).

Método (modelo de envolvente ambiental, tipo BIOCLIM):
  1. Climatología: media de cada variable por celda de la rejilla y mes del año.
  2. Nicho: cada avistamiento se asocia a la celda de mar más cercana y a su mes;
     con los valores ambientales de esas celdas se calculan los percentiles
     (p5..p95) de cada variable para la especie.
  3. Idoneidad de cada celda y mes: para cada variable, 2·min(F, 1-F), donde F es
     la posición del valor en la distribución de la especie (1 = en la mediana,
     0 = fuera de lo observado). La idoneidad es la mínima de todas las variables
     y esa variable se guarda como «factor limitante».

Almacenamiento (almacen.py): climatologia.zarr y un especies/<nombre>.zarr por especie,
con la idoneidad (mes, lat, lon), el factor limitante, los avistamientos por celda y el
nicho; los metadatos y el resumen van en los atributos.

Las especies de ESPECIES_PRINCIPALES se calculan tras cada ejecución del
recolector. Las demás se calculan la primera vez que las pide la web (api.py) y
quedan guardadas; se recalculan solas si la climatología ha cambiado desde entonces.

    python especies.py                    # recalcula la climatología y las principales
    python especies.py "Octopus vulgaris" # calcula una especie concreta
"""

from __future__ import annotations

import fcntl
import logging
import sys
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr
import zarr

import almacen

log = logging.getLogger("especies")

OBIS_URL = "https://api.obis.org/v3"
GEOMETRIA_MEDITERRANEO = "POLYGON((-6 30,36.5 30,36.5 46,-6 46,-6 30))"
MAX_REGISTROS = 20000
ANIO_MINIMO = 2000          # avistamientos más antiguos no reflejan el clima actual
DIAS_REFRESCO_OBIS = 90     # los avistamientos se vuelven a descargar pasado este tiempo
DISTANCIA_MAX_KM = 50       # avistamientos más lejos de cualquier celda de mar se descartan
UMBRAL_IDONEO = 0.2         # idoneidad a partir de la cual una celda se considera favorable
PERCENTILES = [5, 25, 50, 75, 95]

# Cefalópodos de interés pesquero y ecológico en el Mediterráneo.
ESPECIES_PRINCIPALES = {
    "Octopus vulgaris": "Pulpo común",
    "Sepia officinalis": "Sepia común",
    "Loligo vulgaris": "Calamar común",
    "Eledone moschata": "Pulpo almizclado",
    "Eledone cirrhosa": "Pulpo blanco",
    "Illex coindetii": "Pota voladora",
    "Todarodes sagittatus": "Pota europea",
}

# Variables que describen el hábitat. La dirección del viento y la contaminación
# atmosférica quedan fuera. Sólo se usan las que tengan datos.
VARIABLES_HABITAT = ["temperatura", "salinidad", "fitoplancton", "fluorescencia",
                     "dif_temp_mar_aire", "evaporacion", "viento_velocidad"]
VARIABLES_GIOVANNI = {"temperatura", "dif_temp_mar_aire", "evaporacion", "viento_velocidad"}


class ErrorEspecie(Exception):
    pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ruta_especie(nombre: str) -> Path:
    return almacen.DIR_ESPECIES / (nombre.lower().replace(" ", "_") + ".zarr")


# --------------------------------------------------------------------------- climatología

def actualizar_climatologia() -> xr.Dataset:
    """
    Media por celda, variable y mes del año. Las celdas de 0,5° sin datos de Giovanni
    (que sólo se consulta cada 1°) toman el valor de la celda de 1° más cercana.
    """
    ds = almacen.abrir_ambiente()
    variables = [v for v in VARIABLES_HABITAT if ds is not None and v in ds and ds[v].attrs.get("hasta")]
    if not variables:
        raise ErrorEspecie("No hay datos ambientales: ejecuta antes el recolector")
    desde = min(ds[v].attrs["desde"] for v in variables)
    hasta = max(ds[v].attrs["hasta"] for v in variables)

    clima = ds[variables].sel(time=slice(desde, hasta)).groupby("time.month").mean().load()
    for v in variables:
        if v in VARIABLES_GIOVANNI:
            gruesa = clima[v].sel(lat=almacen.LATS[almacen.LATS % 1 == 0], lon=almacen.LONS[almacen.LONS % 1 == 0])
            clima[v] = clima[v].fillna(gruesa.reindex(lat=almacen.LATS, lon=almacen.LONS, method="nearest"))
    clima = clima.where(ds["mar"]).rename(month="mes")
    clima.attrs = {"actualizada": _ahora(), "periodo": f"{desde}..{hasta}"}
    almacen.guardar_dataset(clima, almacen.RUTA_CLIMATOLOGIA)
    log.info("Climatología %s..%s con %s", desde, hasta, variables)
    return clima


def abrir_climatologia() -> xr.Dataset | None:
    if not almacen.RUTA_CLIMATOLOGIA.exists():
        return None
    return xr.open_zarr(almacen.RUTA_CLIMATOLOGIA, consolidated=None).load()


# --------------------------------------------------------------------------- OBIS

def buscar_especies(texto: str, limite: int = 20) -> list[dict]:
    """Autocompletado de nombres científicos (sólo especies)."""
    r = requests.get(f"{OBIS_URL}/taxon/complete/verbose/{texto}", timeout=30)
    r.raise_for_status()
    return [{"nombre": t["scientificName"], "aphia_id": t["id"], "clase": t.get("class"),
             "familia": t.get("family")}
            for t in r.json() if t.get("rank") == "Species"][:limite]


def _taxon(nombre: str) -> dict:
    r = requests.get(f"{OBIS_URL}/taxon/{nombre}", timeout=30)
    r.raise_for_status()
    resultados = [t for t in r.json().get("results", []) if t.get("scientificName", "").lower() == nombre.lower()]
    if not resultados:
        raise ErrorEspecie(f"'{nombre}' no está en OBIS")
    return resultados[0]


def _descargar_avistamientos(nombre: str) -> np.ndarray:
    """Array (n, 3) de lat, lon, mes de los avistamientos desde ANIO_MINIMO. mes=0 si no consta."""
    registros, despues = [], None
    while len(registros) < MAX_REGISTROS:
        params = {"scientificname": nombre, "geometry": GEOMETRIA_MEDITERRANEO,
                  "startdate": f"{ANIO_MINIMO}-01-01", "size": 5000,
                  "fields": "id,decimalLatitude,decimalLongitude,eventDate"}
        if despues:
            params["after"] = despues
        r = requests.get(f"{OBIS_URL}/occurrence", params=params, timeout=120)
        r.raise_for_status()
        lote = r.json().get("results", [])
        if not lote:
            break
        for o in lote:
            fecha = str(o.get("eventDate") or "")
            mes = int(fecha[5:7]) if len(fecha) >= 7 and fecha[4] == "-" and fecha[5:7].isdigit() else 0
            registros.append((o["decimalLatitude"], o["decimalLongitude"], mes if 1 <= mes <= 12 else 0))
        despues = lote[-1]["id"]
        if len(lote) < 5000:
            break
    return np.array(registros, dtype=float).reshape(-1, 3)


def _contar_avistamientos(avist: np.ndarray, mar: np.ndarray) -> np.ndarray:
    """Avistamientos por (mes 0..12, lat, lon), asignados a la celda de mar más cercana."""
    conteo = np.zeros((13, len(almacen.LATS), len(almacen.LONS)), dtype="int32")
    ii, jj = np.nonzero(mar)
    if not len(avist) or not len(ii):
        return conteo
    lat_mar, lon_mar = almacen.LATS[ii], almacen.LONS[jj]
    for lat, lon, mes in avist:
        d = almacen.distancia_km(lat, lon, lat_mar, lon_mar)
        k = int(np.argmin(d))
        if d[k] <= DISTANCIA_MAX_KM:
            conteo[int(mes), ii[k], jj[k]] += 1
    return conteo


# --------------------------------------------------------------------------- cálculo

def calcular_especie(nombre: str, clima: xr.Dataset | None = None, refrescar_obis: bool = False) -> xr.Dataset:
    clima = clima if clima is not None else abrir_climatologia()
    if clima is None:
        clima = actualizar_climatologia()
    mar = clima[list(clima.data_vars)[0]].notnull().any("mes").values

    # Avistamientos: se reutilizan los guardados mientras no caduquen.
    anterior = leer_especie(nombre)
    atributos = dict(anterior.attrs) if anterior is not None else {}
    descargado = atributos.get("obis_descargado")
    if (refrescar_obis or anterior is None or descargado is None or
            datetime.fromisoformat(descargado) < datetime.now(timezone.utc) - timedelta(days=DIAS_REFRESCO_OBIS)):
        taxon = _taxon(nombre)
        avist = _descargar_avistamientos(taxon["scientificName"])
        conteo = _contar_avistamientos(avist, mar)
        atributos.update(aphia_id=taxon.get("taxonID"), registros=len(avist), obis_descargado=_ahora())
        log.info("%s: %d avistamientos desde %d, %d junto a celdas de mar",
                 nombre, len(avist), ANIO_MINIMO, int(conteo.sum()))
    else:
        conteo = anterior["registros"].values

    # 1. Nicho: valor ambiental de cada avistamiento (su mes o, si no consta, la media anual).
    variables = [v for v in VARIABLES_HABITAT if v in clima]
    anual = clima.mean("mes")
    mes_i, lat_i, lon_i = np.nonzero(conteo)
    pesos = conteo[mes_i, lat_i, lon_i]
    muestras = {}
    for v in variables:
        mensual = clima[v].values[np.clip(mes_i - 1, 0, 11), lat_i, lon_i]
        valores = np.where((mes_i > 0) & ~np.isnan(mensual), mensual, anual[v].values[lat_i, lon_i])
        ok = ~np.isnan(valores)
        muestras[v] = np.sort(np.repeat(valores[ok], pesos[ok]))
    variables = [v for v in variables if len(muestras[v]) >= 10]
    if not variables:
        raise ErrorEspecie(f"'{nombre}' tiene muy pocos avistamientos en el Mediterráneo para calcular su nicho")

    # 2. Idoneidad de todas las celdas y meses a la vez: (variable, mes, lat, lon).
    puntuaciones = []
    for v in variables:
        valores = clima[v].values
        f = np.searchsorted(muestras[v], valores) / len(muestras[v])
        puntuaciones.append(np.where(np.isnan(valores), np.nan, 2 * np.minimum(f, 1 - f)))
    puntuaciones = np.stack(puntuaciones)
    hay_dato = ~np.isnan(puntuaciones).all(axis=0)
    idoneidad = np.where(hay_dato, np.nanmin(np.where(np.isnan(puntuaciones), np.inf, puntuaciones), axis=0), np.nan)
    limitante = np.where(hay_dato, np.argmin(np.where(np.isnan(puntuaciones), np.inf, puntuaciones), axis=0), -1)

    # 3. Resumen: % de celdas favorables cada mes y qué variable limita las no favorables.
    favorable = idoneidad >= UMBRAL_IDONEO
    no_favorable = hay_dato & ~favorable
    lim_cuenta = {v: int((limitante[no_favorable] == k).sum()) for k, v in enumerate(variables)}
    total_lim = max(1, sum(lim_cuenta.values()))
    resumen = {
        "variables_usadas": variables,
        "umbral_idoneo": UMBRAL_IDONEO,
        "periodo_climatologia": clima.attrs.get("periodo"),
        "superficie_favorable_pct": {str(m + 1): round(float(100 * favorable[m].sum() / max(1, hay_dato[m].sum())), 1)
                                     for m in range(12) if hay_dato[m].any()},
        "factores_limitantes_pct": {v: round(100 * n / total_lim, 1)
                                    for v, n in sorted(lim_cuenta.items(), key=lambda x: -x[1]) if n},
    }

    coords = {"lat": almacen.LATS, "lon": almacen.LONS}
    ds = xr.Dataset(
        {
            "idoneidad": (("mes", "lat", "lon"), idoneidad.astype("float32")),
            "limitante": (("mes", "lat", "lon"), limitante.astype("int8"),
                          {"codigos": variables, "descripcion": "índice en `codigos`; -1 sin datos"}),
            "registros": (("mes_registro", "lat", "lon"), conteo.astype("int32"),
                          {"descripcion": "avistamientos por celda; mes_registro 0 = mes desconocido"}),
            "nicho": (("variable", "percentil"),
                      np.array([np.percentile(muestras[v], PERCENTILES) for v in variables])),
            "nicho_n": (("variable",), np.array([len(muestras[v]) for v in variables], dtype="int32")),
        },
        coords={**coords, "mes": np.arange(1, 13), "mes_registro": np.arange(13),
                "variable": variables, "percentil": PERCENTILES},
        attrs={**atributos, "nombre": nombre, "nombre_comun": ESPECIES_PRINCIPALES.get(nombre),
               "principal": nombre in ESPECIES_PRINCIPALES, "registros_usados": int(conteo.sum()),
               "climatologia": clima.attrs.get("actualizada"), "calculado": _ahora(), "resumen": resumen},
    )
    almacen.guardar_dataset(ds, ruta_especie(nombre))
    log.info("%s: nicho con %s", nombre, variables)
    return ds


def leer_especie(nombre: str) -> xr.Dataset | None:
    ruta = ruta_especie(nombre)
    if not ruta.exists():
        return None
    return xr.open_zarr(ruta, consolidated=None)


def atributos_especie(ruta: Path) -> dict:
    """Sólo los metadatos (sin abrir los arrays): para listar especies deprisa."""
    return dict(zarr.open_group(ruta, mode="r").attrs)


def listar_especies() -> list[dict]:
    if not almacen.DIR_ESPECIES.exists():
        return []
    especies = []
    for ruta in sorted(almacen.DIR_ESPECIES.glob("*.zarr")):
        if ruta.name.endswith((".tmp.zarr", ".old.zarr")):
            continue
        a = atributos_especie(ruta)
        especies.append({k: a.get(k) for k in ("nombre", "nombre_comun", "principal", "registros",
                                                 "registros_usados", "calculado")})
    return sorted(especies, key=lambda e: (not e["principal"], e["nombre"]))


def esta_al_dia(nombre: str) -> bool:
    ruta = ruta_especie(nombre)
    if not ruta.exists() or not almacen.RUTA_CLIMATOLOGIA.exists():
        return False
    clima = zarr.open_group(almacen.RUTA_CLIMATOLOGIA, mode="r").attrs.get("actualizada")
    return atributos_especie(ruta).get("climatologia") == clima


_cerrojos: dict[str, threading.Lock] = defaultdict(threading.Lock)


@contextmanager
def _cerrojo(nombre: str):
    """Exclusión entre hilos y entre procesos (varios workers de uvicorn) para una especie."""
    almacen.DIR_ESPECIES.mkdir(parents=True, exist_ok=True)
    ruta_lock = ruta_especie(nombre).with_suffix(".lock")
    with _cerrojos[nombre], open(ruta_lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if not ruta_especie(nombre).exists():
                ruta_lock.unlink(missing_ok=True)  # nombres que no existen no dejan ficheros
            fcntl.flock(f, fcntl.LOCK_UN)


def asegurar_especie(nombre: str) -> None:
    """Calcula la especie si no está calculada o si la climatología ha cambiado."""
    if esta_al_dia(nombre):
        return
    with _cerrojo(nombre):
        if not esta_al_dia(nombre):  # otro proceso pudo calcularla mientras esperábamos
            calcular_especie(nombre)


def calcular_principales() -> None:
    """Tras cada ejecución del recolector: climatología nueva y especies principales."""
    clima = actualizar_climatologia()
    for nombre in ESPECIES_PRINCIPALES:
        try:
            with _cerrojo(nombre):
                calcular_especie(nombre, clima)
        except Exception as e:  # una especie que falla no detiene las demás
            log.error("%s: %s", nombre, e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    if sys.argv[1:]:
        for nombre in sys.argv[1:]:
            with _cerrojo(nombre):
                calcular_especie(nombre, refrescar_obis=True)
    else:
        calcular_principales()


if __name__ == "__main__":
    main()
