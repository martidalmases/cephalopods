"""
Relación entre las especies marinas y las condiciones ambientales, AÑO A AÑO y en todo el planeta.

Sólo usa las variables ambientales guardadas por el recolector (medias mensuales desde
1980, rejilla mundial). De fuera se obtienen únicamente los avistamientos de cada especie
(OBIS, sin registro).

Método (modelo de envolvente ambiental, tipo BIOCLIM):
  1. Medias anuales: media de cada variable por celda y año (con al menos MESES_MINIMOS
     meses con dato; el año en curso, incompleto, queda fuera).
  2. Nicho: cada avistamiento se asocia a la celda de mar más cercana y a su año; con
     los valores de ese año en esas celdas se calculan los percentiles (p5..p95) de cada
     variable para la especie. Si no consta el año, o la variable no existía ese año,
     se usa la media de todos los años de esa celda.
  3. Idoneidad de cada celda y año: para cada variable, 2·min(F, 1-F), donde F es la
     posición del valor en la distribución de la especie (1 = en la mediana, 0 = fuera
     de lo observado). La idoneidad es la mínima de todas las variables y esa variable
     se guarda como «factor limitante».

Para que la serie anual sea comparable de principio a fin, el índice de idoneidad sólo
usa las variables con datos en (casi) todo el periodo: con MERRA-2, de 1980 a hoy. Las
de satélites más recientes (clorofila desde 2003, salinidad desde 2015) aparecen en el
nicho pero no entran en el índice.

Memoria: todo se calcula variable a variable (y las medias anuales, además, por franjas de
latitud), sin cargar nunca a la vez todas las variables del planeta; el pico es de unos
cientos de MB.

Almacenamiento (almacen.py): anual.zarr y un especies/<nombre>.zarr por especie, con la
idoneidad y el factor limitante (anio, lat, lon), los avistamientos por año y celda y el
nicho; los metadatos y el resumen van en los atributos.

Las especies de ESPECIES_PRINCIPALES se calculan tras cada ejecución del recolector. Las
demás se calculan la primera vez que las pide la web (api.py) y quedan guardadas; se
recalculan solas si las medias anuales han cambiado desde entonces.

    python especies.py                    # recalcula las medias anuales y las principales
    python especies.py "Octopus vulgaris" # calcula una especie concreta
"""

from __future__ import annotations

import fcntl
import logging
import sys
import threading
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import numpy as np
import requests
import xarray as xr
import zarr

import almacen

log = logging.getLogger("especies")

OBIS_URL = "https://api.obis.org/v3"
MAX_REGISTROS = 200000      # avistamientos por especie en todo el mundo
ANIO_MINIMO = 1980          # mismo inicio que los datos ambientales
MESES_MINIMOS = 9           # meses con dato para que un año cuente en una celda
COBERTURA_LARGA = 0.8       # fracción de años con dato para que una variable entre en el índice
DIAS_REFRESCO_OBIS = 90     # los avistamientos se vuelven a descargar pasado este tiempo
R = almacen.FINA                            # rejilla de las especies y de las medias anuales (0,5°)
DISTANCIA_MAX_KM = R.paso * 111.2          # avistamiento en tierra: celda de mar vecina a menos de esto
BASE_MHW = (1982, 2011)                     # periodo de referencia de las olas de calor marinas
UMBRAL_IDONEO = 0.2         # idoneidad a partir de la cual una celda se considera favorable
PERCENTILES = [5, 25, 50, 75, 95]
FRANJA = 45                 # filas de latitud por franja al calcular medias anuales
TROZOS = {"anio": 12, "anio_registro": 12, "lat": almacen.TROZO_ESPACIO, "lon": almacen.TROZO_ESPACIO}

# Especies tropicales que han entrado en el Mediterráneo por el canal de Suez (lessepsianas):
# se calculan siempre; cualquier otra se calcula cuando se pide.
ESPECIES_PRINCIPALES = {
    "Pterois miles": "Pez león",
    "Lagocephalus sceleratus": "Pez globo plateado",
    "Siganus luridus": "Pez conejo oscuro",
    "Fistularia commersonii": "Pez corneta",
}

# Variables que describen el hábitat. La dirección del viento y los aerosoles quedan fuera.
# Sólo se usan las que tengan datos. Las tres primeras salen de la temperatura del mar de
# OISST (rejilla FINA, ver extremos_sst); la clorofila también es FINA; el resto es de la
# rejilla BASE (1°) y se repite en las 4 celdas de 0,5° que cubre cada celda.
VARIABLES_SST = ["sst_winter_min", "sst_summer_max", "mhw_months"]
VARIABLES_HABITAT = VARIABLES_SST + ["fitoplancton", "salinidad", "fluorescencia",
                                     "dif_temp_mar_aire", "evaporacion", "viento_velocidad"]


class ErrorEspecie(Exception):
    pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ruta_especie(nombre: str) -> Path:
    return almacen.DIR_ESPECIES / (nombre.lower().replace(" ", "_") + ".zarr")


def _mar() -> np.ndarray:
    """Máscara de mar de la rejilla FINA (de OISST); si aún no hay, la BASE ampliada."""
    g = almacen.abrir_grupo(almacen.FINA)
    if g is not None and g["mar"][:].any():
        return g["mar"][:]
    return almacen.ampliar(almacen.abrir_grupo(almacen.BASE)["mar"][:])


# --------------------------------------------------------------------------- medias anuales

def extremos_sst(g_fino, anios: np.ndarray, franja: int = FRANJA) -> dict[str, np.ndarray]:
    """
    Por año y celda, a partir de la temperatura del mar mensual (OISST):
      sst_winter_min  la media mensual más baja del año (el mes más frío);
      sst_summer_max  la más alta (el mes más cálido);
      mhw_months      meses de ola de calor marina: meses por encima del percentil 90 de ese
                      mes del año en BASE_MHW. Es una versión mensual de la definición de
                      Hobday et al. (2016), que usa datos diarios.
    Hacen falta MESES_MINIMOS meses con dato en el año. Por franjas de latitud: poca RAM.
    """
    nlat, nlon = g_fino["sst"].shape[1:]
    salida = {v: np.full((len(anios), nlat, nlon), np.nan, dtype="float32") for v in VARIABLES_SST}
    en_base = (anios >= BASE_MHW[0]) & (anios <= BASE_MHW[1])
    for a in range(0, nlat, franja):
        b = min(a + franja, nlat)
        m = almacen.meses_anios(g_fino, "sst", anios, a, b)           # (años, 12, filas, columnas)
        completo = (~np.isnan(m)).sum(axis=1) >= MESES_MINIMOS
        with warnings.catch_warnings():                                # celdas de tierra: todo NaN
            warnings.simplefilter("ignore", RuntimeWarning)
            salida["sst_winter_min"][:, a:b] = np.where(completo, np.nanmin(m, axis=1), np.nan)
            salida["sst_summer_max"][:, a:b] = np.where(completo, np.nanmax(m, axis=1), np.nan)
            referencia = m[en_base]
            umbral = np.nanpercentile(referencia, 90, axis=0)           # (12, filas, columnas)
            umbral[(~np.isnan(referencia)).sum(axis=0) < 10] = np.nan   # al menos 10 años de referencia
            calor = (m > umbral[None]).sum(axis=1).astype("float32")
        salida["mhw_months"][:, a:b] = np.where(completo & ~np.isnan(umbral).all(axis=0), calor, np.nan)
    return salida


def actualizar_anual() -> xr.Dataset:
    """
    Medias y extremos anuales de las variables de hábitat en la rejilla FINA, de 1980 al
    último año completo. Una variable cada vez: el pico de RAM es de unos cientos de MB.
    """
    g_base, g_fino = almacen.abrir_grupo(almacen.BASE), almacen.abrir_grupo(almacen.FINA)
    if g_base is None and g_fino is None:
        raise ErrorEspecie("No hay datos ambientales: ejecuta antes el recolector")
    anios = np.arange(almacen.ORIGEN.year, date.today().year)          # hasta el último año completo
    mar = _mar()
    datos = {}

    def guardar(v: str, anual: np.ndarray, unidad: str):
        anual[:, ~mar] = np.nan
        datos[v] = (("anio", "lat", "lon"), anual, {"units": unidad})

    if g_fino is not None and "sst" in g_fino and g_fino["sst"].attrs.get("hasta"):
        for v, anual in extremos_sst(g_fino, anios).items():
            guardar(v, anual, "meses" if v == "mhw_months" else "°C")
    for v in VARIABLES_HABITAT[len(VARIABLES_SST):]:
        if g_fino is not None and v in g_fino and g_fino[v].attrs.get("hasta"):
            guardar(v, almacen.medias_anuales(g_fino, v, anios, MESES_MINIMOS, FRANJA), g_fino[v].attrs.get("units", ""))
        elif g_base is not None and v in g_base and g_base[v].attrs.get("hasta"):
            gruesa = almacen.medias_anuales(g_base, v, anios, MESES_MINIMOS, FRANJA)
            guardar(v, almacen.ampliar(gruesa), g_base[v].attrs.get("units", ""))
            del gruesa
    if not datos:
        raise ErrorEspecie("No hay datos ambientales: ejecuta antes el recolector")

    ds = xr.Dataset(datos, coords={"anio": anios, "lat": R.lats, "lon": R.lons},
                    attrs={"actualizada": _ahora(), "meses_minimos": MESES_MINIMOS, "paso_grados": R.paso})
    almacen.guardar_dataset(ds, almacen.RUTA_ANUAL, TROZOS)
    log.info("Medias anuales %d-%d (%s°) con %s", anios[0], anios[-1], R.paso, list(datos))
    return xr.open_zarr(almacen.RUTA_ANUAL, consolidated=None)


def abrir_anual() -> xr.Dataset | None:
    """Se abre sin cargar: cada variable se lee cuando se usa."""
    if not almacen.RUTA_ANUAL.exists():
        return None
    return xr.open_zarr(almacen.RUTA_ANUAL, consolidated=None)


def _elegir_indice(anual: xr.Dataset) -> tuple[list[str], list[int]]:
    """Variables del índice anual y años en que todas tienen datos (ver docstring del módulo)."""
    hay = {v: anual[v].notnull().any(("lat", "lon")).values for v in anual.data_vars}
    n_anios = len(anual.anio)
    largas = [v for v in VARIABLES_HABITAT if v in hay and hay[v].sum() >= COBERTURA_LARGA * n_anios]
    variables = largas or [v for v in VARIABLES_HABITAT if v in hay and hay[v].any()]
    # Años comunes; si no hay ninguno, se quita la variable más corta hasta que los haya.
    while variables:
        comunes = np.logical_and.reduce([hay[v] for v in variables])
        if comunes.any():
            return variables, [int(a) for a in anual.anio.values[comunes]]
        variables.remove(min(variables, key=lambda v: hay[v].sum()))
    raise ErrorEspecie("No hay ningún año con datos suficientes")


# --------------------------------------------------------------------------- OBIS

@lru_cache(maxsize=512)
def _completar_obis(texto: str) -> tuple[dict, ...]:
    """Autocompletado de OBIS (máximo 20 taxones de cualquier rango, por orden alfabético)."""
    r = requests.get(f"{OBIS_URL}/taxon/complete/verbose/{quote(texto)}", timeout=30)
    r.raise_for_status()
    return tuple(r.json())


@lru_cache(maxsize=512)
def _especies_de(taxon: str, cuantas: int = 1000) -> tuple[dict, ...]:
    """
    Especies aceptadas de un taxón (género, familia, orden...) con su número de registros en
    OBIS. La lista de comprobación de OBIS ya viene ordenada de más a menos registros.
    """
    r = requests.get(f"{OBIS_URL}/checklist", params={"scientificname": taxon, "size": cuantas}, timeout=60)
    if r.status_code == 404:
        return ()
    r.raise_for_status()
    return tuple({"nombre": t["scientificName"], "aphia_id": t.get("taxonID"), "clase": t.get("class"),
                  "familia": t.get("family"), "registros": int(t.get("records") or 0)}
                 for t in r.json().get("results", [])
                 if t.get("taxonRank") == "Species" and t.get("taxonomicStatus", "accepted") == "accepted")


def buscar_especies(texto: str, limite: int = 15) -> list[dict]:
    """
    Autocompletado de nombres científicos de especies con registros en OBIS. Primero las que
    empiezan por lo escrito y, dentro de cada grupo, las más registradas.
      * «Octopus v»: especies del género Octopus cuyo epíteto empieza por «v».
      * «thunn»: especies de los géneros que empiezan por «thunn» (Thunnus...).
      * «octo»: además, especies de los grupos superiores que empiezan así (Octopoda...).
    El autocompletado de OBIS sólo da 20 taxones de cualquier rango y en orden alfabético
    (con «octo» no sale ninguna especie), así que se usa para encontrar géneros y grupos, y
    después se piden sus especies con su número de registros.
    """
    # Catálogo local de todas las especies de OBIS (catalogo_especies.py): sin consultar
    # OBIS en cada tecla. Si todavía no se ha descargado, se busca en línea como antes.
    import catalogo_especies
    local = catalogo_especies.buscar(texto, limite)
    if local is not None:
        return local

    palabras = texto.split()
    if not palabras:
        return []
    primera = palabras[0].capitalize()
    resto = " ".join(palabras[1:]).lower()
    # Taxones candidatos: el género escrito tal cual (si está completo), los géneros que
    # empiezan así y, si es una sola palabra, los grupos superiores que empiezan así.
    generos, grupos = [primera], []
    for t in _completar_obis(primera if not resto else " ".join(palabras)):
        nombre, rango = t.get("scientificName", ""), t.get("rank")
        if rango == "Species":
            nombre, rango = nombre.split()[0], "Genus"
        if not nombre.lower().startswith(primera.lower()):
            continue
        if rango == "Genus" and nombre not in generos:
            generos.append(nombre)
        elif rango not in ("Genus", "Subgenus", "Subspecies", "Variety", "Form") and not resto:
            grupos.append(nombre)
    if resto:   # «Género epíteto»: sólo ese género (o el más parecido)
        generos = [g for g in generos if g.lower() == primera.lower()][:1] or generos[:3]
    tareas = [(g, 1000) for g in generos[:6]] + [(g, 300) for g in grupos[:3]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        listas = list(pool.map(lambda t: _especies_de(*t), tareas))
    vistos, especies = set(), []
    for lista in listas:
        for e in lista:
            epiteto = e["nombre"].split(" ", 1)[1].lower() if " " in e["nombre"] else ""
            if e["nombre"] in vistos or e["registros"] == 0 or (resto and not epiteto.startswith(resto)):
                continue
            vistos.add(e["nombre"])
            especies.append(e)
    empieza = " ".join(palabras).lower()
    especies.sort(key=lambda e: (not e["nombre"].lower().startswith(empieza), -e["registros"]))
    return especies[:limite]


def _taxon(nombre: str) -> dict:
    r = requests.get(f"{OBIS_URL}/taxon/{nombre}", timeout=30)
    r.raise_for_status()
    resultados = [t for t in r.json().get("results", []) if t.get("scientificName", "").lower() == nombre.lower()]
    if not resultados:
        raise ErrorEspecie(f"'{nombre}' no está en OBIS")
    return resultados[0]


def _descargar_avistamientos(nombre: str) -> np.ndarray:
    """Array (n, 3) float32 de lat, lon, año de los avistamientos desde ANIO_MINIMO. año=0 si no consta."""
    partes, n, despues = [], 0, None
    while n < MAX_REGISTROS:
        params = {"scientificname": nombre, "startdate": f"{ANIO_MINIMO}-01-01", "size": 5000,
                  "fields": "id,decimalLatitude,decimalLongitude,date_year"}
        if despues:
            params["after"] = despues
        r = requests.get(f"{OBIS_URL}/occurrence", params=params, timeout=180)
        r.raise_for_status()
        lote = r.json().get("results", [])
        if not lote:
            break
        bloque = np.array([(o["decimalLatitude"], o["decimalLongitude"], o.get("date_year") or 0) for o in lote],
                          dtype="float32")
        bloque[(bloque[:, 2] < ANIO_MINIMO) | (bloque[:, 2] > 2100), 2] = 0
        partes.append(bloque)
        n += len(lote)
        despues = lote[-1]["id"]
        if len(lote) < 5000:
            break
    return np.concatenate(partes) if partes else np.zeros((0, 3), dtype="float32")


def _contar_avistamientos(avist: np.ndarray, mar: np.ndarray, anios: np.ndarray) -> np.ndarray:
    """
    Avistamientos por (año, lat, lon). Cada uno va a su celda si es de mar; si cae en tierra,
    a la celda de mar vecina más cercana (a menos de DISTANCIA_MAX_KM). Índice 0 = año desconocido.
    """
    conteo = np.zeros((len(anios), R.nlat, R.nlon), dtype="int32")
    if not len(avist):
        return conteo
    lat, lon, anio = avist[:, 0].astype(float), avist[:, 1].astype(float), avist[:, 2].astype(int)
    i, j = almacen.indices_celda(lat, lon, R)
    # Para todos, las 9 celdas del entorno; nos quedamos con la de mar más cercana.
    mejor_i, mejor_j, mejor_d = i.copy(), j.copy(), np.where(mar[i, j], 0.0, np.inf)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == dj == 0:
                continue
            vi, vj = np.clip(i + di, 0, R.nlat - 1), (j + dj) % R.nlon
            d = np.where(mar[vi, vj], almacen.distancia_km(lat, lon, R.lats[vi], R.lons[vj]), np.inf)
            cerca = d < mejor_d
            mejor_i, mejor_j, mejor_d = np.where(cerca, vi, mejor_i), np.where(cerca, vj, mejor_j), np.minimum(d, mejor_d)
    ok = mejor_d <= DISTANCIA_MAX_KM
    posicion = np.searchsorted(anios, anio)
    posicion = np.where((posicion < len(anios)) & (anios[np.clip(posicion, 0, len(anios) - 1)] == anio), posicion, 0)
    np.add.at(conteo, (posicion[ok], mejor_i[ok], mejor_j[ok]), 1)
    return conteo


# --------------------------------------------------------------------------- cálculo

def calcular_especie(nombre: str, anual: xr.Dataset | None = None, refrescar_obis: bool = False) -> xr.Dataset:
    anual = anual if anual is not None else abrir_anual()
    if anual is None:
        anual = actualizar_anual()
    mar = _mar()
    # Años de los avistamientos: 0 = desconocido, luego ANIO_MINIMO..año actual.
    anios_registro = np.concatenate([[0], np.arange(ANIO_MINIMO, date.today().year + 1)])

    # Avistamientos: se reutilizan los guardados mientras no caduquen.
    anterior = leer_especie(nombre)
    atributos = dict(anterior.attrs) if anterior is not None else {}
    descargado = atributos.get("obis_descargado")
    if (refrescar_obis or anterior is None or "anio_registro" not in anterior.dims
            or anterior.sizes.get("lat") != R.nlat or descargado is None
            or datetime.fromisoformat(descargado) < datetime.now(timezone.utc) - timedelta(days=DIAS_REFRESCO_OBIS)):
        taxon = _taxon(nombre)
        avist = _descargar_avistamientos(taxon["scientificName"])
        conteo = _contar_avistamientos(avist, mar, anios_registro)
        atributos.update(aphia_id=taxon.get("taxonID"), registros=len(avist), obis_descargado=_ahora())
        log.info("%s: %d avistamientos desde %d, %d junto a celdas de mar",
                 nombre, len(avist), ANIO_MINIMO, int(conteo.sum()))
        del avist
    else:
        conteo = anterior["registros"].reindex(anio_registro=anios_registro, fill_value=0).values

    # 1. Nicho: valor de cada variable en la celda y el año de cada avistamiento.
    anios = anual.anio.values
    k_reg, lat_i, lon_i = np.nonzero(conteo)
    pesos = conteo[k_reg, lat_i, lon_i]
    anio_reg = anios_registro[k_reg]
    pos_anio = np.clip(np.searchsorted(anios, anio_reg), 0, len(anios) - 1)
    conocido = anios[pos_anio] == anio_reg
    muestras = {}
    for v in [v for v in VARIABLES_HABITAT if v in anual]:
        valores_v = anual[v].values                                 # (años, lat, lon) de una variable
        del_anio = valores_v[pos_anio, lat_i, lon_i]
        with warnings.catch_warnings():                            # celdas sin ningún año: NaN
            warnings.simplefilter("ignore", RuntimeWarning)
            media = np.nanmean(valores_v[:, lat_i, lon_i], axis=0)   # sólo las celdas con avistamientos
        valores = np.where(conocido & ~np.isnan(del_anio), del_anio, media)
        ok = ~np.isnan(valores)
        if pesos[ok].sum() >= 10:
            muestras[v] = np.sort(np.repeat(valores[ok], pesos[ok]).astype("float32"))
        del valores_v
    if not muestras:
        raise ErrorEspecie(f"'{nombre}' tiene muy pocos avistamientos para calcular su nicho")
    variables_nicho = list(muestras)

    # 2. Idoneidad de todas las celdas y años del periodo, variable a variable (mínimo acumulado).
    variables, periodo = _elegir_indice(anual)
    variables = [v for v in variables if v in muestras]
    if not variables:
        raise ErrorEspecie(f"'{nombre}' no tiene avistamientos en celdas con las variables del índice")
    forma = (len(periodo), R.nlat, R.nlon)
    idoneidad = np.full(forma, np.inf, dtype="float32")
    limitante = np.full(forma, -1, dtype="int8")
    for k, v in enumerate(variables):
        valores = anual[v].sel(anio=periodo).values
        f = (np.searchsorted(muestras[v], valores) / len(muestras[v])).astype("float32")
        puntuacion = np.where(np.isnan(valores), np.inf, 2 * np.minimum(f, 1 - f))
        menor = puntuacion < idoneidad
        idoneidad = np.where(menor, puntuacion, idoneidad)
        limitante[menor] = k
        del valores, f, puntuacion, menor
    hay_dato = limitante >= 0
    idoneidad[~hay_dato] = np.nan

    # 3. Resumen: % de mar favorable cada año, su tendencia y qué variable limita.
    favorable = idoneidad >= UMBRAL_IDONEO
    pct = {str(a): round(float(100 * favorable[k].sum() / max(1, hay_dato[k].sum())), 1) for k, a in enumerate(periodo)}
    pendiente = float(np.polyfit(periodo, list(pct.values()), 1)[0] * 10) if len(periodo) >= 3 else None
    no_favorable = hay_dato & ~favorable
    lim_cuenta = {v: int((limitante[no_favorable] == k).sum()) for k, v in enumerate(variables)}
    total_lim = max(1, sum(lim_cuenta.values()))
    por_anio = conteo.sum(axis=(1, 2))
    resumen = {
        "variables_indice": variables,
        "variables_nicho": variables_nicho,
        "periodo": [periodo[0], periodo[-1]],
        "umbral_idoneo": UMBRAL_IDONEO,
        "superficie_favorable_pct": pct,
        # Puntos porcentuales de mar favorable por década (recta de mínimos cuadrados).
        "tendencia_pp_por_decada": round(pendiente, 2) if pendiente is not None else None,
        "factores_limitantes_pct": {v: round(100 * n / total_lim, 1)
                                    for v, n in sorted(lim_cuenta.items(), key=lambda x: -x[1]) if n},
        "avistamientos_por_anio": {str(int(a)): int(n) for a, n in zip(anios_registro, por_anio) if a and n},
        "avistamientos_sin_anio": int(por_anio[0]),
    }

    ds = xr.Dataset(
        {
            "idoneidad": (("anio", "lat", "lon"), idoneidad),
            "limitante": (("anio", "lat", "lon"), limitante,
                          {"codigos": variables, "descripcion": "índice en `codigos`; -1 sin datos"}),
            "registros": (("anio_registro", "lat", "lon"), conteo,
                          {"descripcion": "avistamientos por celda y año; anio_registro 0 = año desconocido"}),
            "nicho": (("variable", "percentil"),
                      np.array([np.percentile(muestras[v], PERCENTILES) for v in variables_nicho], dtype=float)),
            "nicho_n": (("variable",), np.array([len(muestras[v]) for v in variables_nicho], dtype="int32")),
        },
        coords={"lat": R.lats, "lon": R.lons, "anio": periodo, "anio_registro": anios_registro,
                "variable": variables_nicho, "percentil": PERCENTILES},
        attrs={**atributos, "nombre": nombre, "nombre_comun": ESPECIES_PRINCIPALES.get(nombre),
               "principal": nombre in ESPECIES_PRINCIPALES, "registros_usados": int(conteo.sum()),
               "anual": anual.attrs.get("actualizada"), "calculado": _ahora(), "resumen": resumen,
               "paso_grados": R.paso},
    )
    almacen.guardar_dataset(ds, ruta_especie(nombre), TROZOS)
    log.info("%s: índice %d-%d con %s; nicho con %s", nombre, periodo[0], periodo[-1], variables, variables_nicho)
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
    if not ruta.exists() or not almacen.RUTA_ANUAL.exists():
        return False
    anual = zarr.open_group(almacen.RUTA_ANUAL, mode="r").attrs.get("actualizada")
    return atributos_especie(ruta).get("anual") == anual


_cerrojos: dict[str, threading.Lock] = defaultdict(threading.Lock)
# Un solo cálculo a la vez por proceso: limita el pico de RAM si piden varias especies nuevas.
_cerrojo_calculo = threading.Lock()


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
    """Calcula la especie si no está calculada o si las medias anuales han cambiado."""
    if esta_al_dia(nombre):
        return
    with _cerrojo(nombre), _cerrojo_calculo:
        if not esta_al_dia(nombre):  # otro proceso pudo calcularla mientras esperábamos
            calcular_especie(nombre)


def calcular_principales() -> None:
    """Tras cada ejecución del recolector: medias anuales nuevas y especies principales."""
    anual = actualizar_anual()
    for nombre in ESPECIES_PRINCIPALES:
        try:
            with _cerrojo(nombre):
                calcular_especie(nombre, anual)
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
