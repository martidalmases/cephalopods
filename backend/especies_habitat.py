"""
Hábitat de una especie con las cuatro variables elegidas, a 0,25°. Es el cálculo que pide la
web (portal de especies) para cualquier especie de OBIS:

  1. Avistamientos de TODO EL MUNDO desde 1980 (OBIS). Cada uno va a su celda de 0,25°; si cae
     en tierra, a la celda de mar vecina (dos celdas como mucho). Se quedan las PRESENCIAS:
     combinaciones distintas de celda y año (mil registros del mismo sitio y año cuentan una vez).
  2. Perfil ambiental: a cada presencia se le asigna el valor de cada variable EN SU AÑO
     (almacén de hábitat, habitat.py). Los percentiles 5, 25, 50, 75 y 95 de esos valores son
     el perfil de la especie; también se da el perfil año a año.
  3. Idoneidad del hábitat en el Mediterráneo (0 a 1), por celda y año: para cada variable,
     2·min(F, 1-F), donde F es la posición del valor de la celda dentro de los valores de las
     presencias (1 = en la mediana, 0 = fuera de lo observado); la idoneidad es la menor de
     las variables y esa variable es el «factor limitante» (envolvente ambiental tipo BIOCLIM).
     Los meses anómalamente cálidos sólo limitan por arriba (ver LADO).
     El índice 1980-hoy usa las tres variables de temperatura; con clorofila sólo existe desde
     2003 y se da como segunda capa.
  4. Indicadores anuales y su tendencia, todos con el mismo método (tendencias.py: pendiente de
     Sen, Mann-Kendall modificado y corrección FDR sobre el conjunto de indicadores):
       - superficie idónea del Mediterráneo (km²);
       - latitud del centro y del límite hacia el polo del hábitat idóneo;
       - límite hacia el polo OBSERVADO en cada hemisferio (percentil 95 de la latitud
         absoluta de las presencias, por ventanas de años);
       - tolerancia térmica: cómo cambian el límite frío, el centro y el límite cálido de las
         temperaturas donde se observa la especie, comparado con cuánto se ha calentado el
         mar en esos mismos sitios.
     Y la tendencia de la idoneidad en cada celda (FDR sobre las celdas del mapa).

Los indicadores observados dependen de dónde y cuándo se ha muestreado: por eso cada ventana
se reduce al mismo número de presencias antes de calcular percentiles, y se indica n.

Resultado: DATOS_DIR/habitat/especies/<nombre>.json (lo sirve api.py tal cual) y un
<nombre>.resumen.json pequeño para la lista. Se recalcula si cambia el almacén de hábitat.

    python especies_habitat.py                      # las especies principales
    python especies_habitat.py "Thalassoma pavo"    # una especie concreta
"""

from __future__ import annotations

import fcntl
import json
import logging
import sys
import threading
import warnings
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import habitat
import regiones
import tendencias

log = logging.getLogger("especies_habitat")

DIR = habitat.DIR / "especies"
UMBRAL_IDONEO = 0.2          # idoneidad a partir de la cual una celda cuenta como hábitat idóneo
PERCENTILES = [5, 25, 50, 75, 95]
MIN_PRESENCIAS = 30          # presencias (celda y año) para dar un perfil
MIN_ANIO_PERFIL = 10         # presencias en un año para dar su perfil anual
MIN_VENTANA = 30             # presencias por ventana en los indicadores observados
ANCHOS_VENTANA = (1, 3, 5)   # años por ventana: se usa el menor que dé ventanas suficientes
REMUESTREOS = 50             # submuestras al igualar el tamaño de las ventanas
# Los meses anómalamente cálidos sólo cuentan en contra cuando hay MÁS de los que la especie ha
# soportado: tener pocos nunca hace peor un sitio. Las demás variables limitan por los dos lados.
LADO = {"mhw_months": "superior"}
DISTANCIA_CELDAS = 2         # avistamiento en tierra: celda de mar a esta distancia como mucho
DIAS_REFRESCO_OBIS = 90
KM_POR_GRADO = 111.195
VERSION_CALCULO = 1          # se sube al cambiar el método: obliga a recalcular lo guardado


class ErrorEspecie(Exception):
    pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(nombre: str) -> str:
    return nombre.lower().replace(" ", "_")


def ruta(nombre: str) -> Path:
    return DIR / (_slug(nombre) + ".json")


def _escribir(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    tmp.write_text(texto)
    tmp.replace(destino)


def _lista(a, decimales: int) -> list:
    """Array a lista para JSON: redondeado y con None donde hay NaN."""
    a = np.asarray(a, dtype=float)
    return [None if not np.isfinite(x) else round(float(x), decimales) for x in a.ravel()]


# --------------------------------------------------------------------------- presencias

def presencias(avist: np.ndarray, mar: np.ndarray, anios: tuple[int, int]) -> dict:
    """
    De avistamientos (n, 3) [lat, lon, año] a presencias: celdas de mar y año distintos.
    Devuelve i, j (celda de 0,25°), anio, n (registros de cada presencia) y el recuento de lo descartado.
    """
    lat, lon, anio = avist[:, 0].astype(float), avist[:, 1].astype(float), avist[:, 2].astype(int)
    con_anio = (anio >= anios[0]) & (anio <= anios[1])
    lat, lon, anio = lat[con_anio], lon[con_anio], anio[con_anio]
    i, j = habitat.indices(lat, lon)
    mejor_i, mejor_j = i.copy(), j.copy()
    mejor_d = np.where(mar[i, j], 0.0, np.inf)
    r = DISTANCIA_CELDAS
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            if di == dj == 0:
                continue
            vi, vj = np.clip(i + di, 0, habitat.NLAT - 1), (j + dj) % habitat.NLON
            d = np.where(mar[vi, vj], np.hypot(di, dj * np.cos(np.deg2rad(lat))), np.inf)
            cerca = d < mejor_d
            mejor_i, mejor_j, mejor_d = np.where(cerca, vi, mejor_i), np.where(cerca, vj, mejor_j), np.minimum(d, mejor_d)
    en_mar = np.isfinite(mejor_d)
    clave = (anio[en_mar].astype(np.int64) * habitat.NLAT + mejor_i[en_mar]) * habitat.NLON + mejor_j[en_mar]
    unicas, cuenta = np.unique(clave, return_counts=True)
    return {"anio": (unicas // (habitat.NLAT * habitat.NLON)).astype(int),
            "i": ((unicas // habitat.NLON) % habitat.NLAT).astype(int), "j": (unicas % habitat.NLON).astype(int),
            "n": cuenta.astype(int), "registros": int(len(avist)), "sin_anio": int((~con_anio).sum()),
            "en_tierra": int((~en_mar).sum())}


# --------------------------------------------------------------------------- perfil e idoneidad

def _posicion(muestra: np.ndarray, valores: np.ndarray) -> np.ndarray:
    """Posición (0 a 1) de cada valor dentro de la muestra ordenada; con empates, el punto medio."""
    izq = np.searchsorted(muestra, valores, "left")
    der = np.searchsorted(muestra, valores, "right")
    return (izq + der) / (2.0 * len(muestra))


def puntuacion(muestra: np.ndarray, valores: np.ndarray, lado: str = "ambos") -> np.ndarray:
    """
    Idoneidad de una variable. "ambos": 2·min(F, 1-F), 1 en la mediana de la especie y 0 fuera de
    lo observado por arriba o por abajo. "superior": sólo penaliza por arriba, min(1, 2·(1-F)).
    NaN donde no hay dato.
    """
    f = _posicion(muestra, valores)
    s = np.minimum(1.0, 2 * (1 - f)) if lado == "superior" else 2 * np.minimum(f, 1 - f)
    return np.where(np.isfinite(valores), s, np.nan).astype("float32")


def _percentiles_ponderados(valores: np.ndarray, pesos: np.ndarray, q: float) -> float:
    orden = np.argsort(valores)
    acumulado = np.cumsum(pesos[orden])
    return float(valores[orden][np.searchsorted(acumulado, q / 100 * acumulado[-1])])


# --------------------------------------------------------------------------- ventanas (observado)

def elegir_ventanas(anio: np.ndarray, desde: int, hasta: int) -> tuple[list[tuple[int, int]], int] | None:
    """Ventanas de años consecutivos con al menos MIN_VENTANA presencias; None si no hay bastantes."""
    for ancho in ANCHOS_VENTANA:
        ventanas = []
        for a in range(desde, hasta + 1, ancho):
            b = min(a + ancho - 1, hasta)
            if b - a + 1 == ancho and ((anio >= a) & (anio <= b)).sum() >= MIN_VENTANA:
                ventanas.append((a, b))
        if len(ventanas) >= tendencias.MINIMO:
            return ventanas, ancho
    return None


def serie_por_ventanas(anio: np.ndarray, ventanas: list[tuple[int, int]], funciones: dict,
                       rng: np.random.Generator) -> tuple[dict[str, np.ndarray], int]:
    """
    Estadísticos por ventana con el MISMO número de presencias en todas (el de la ventana con
    menos): se sacan REMUESTREOS submuestras al azar y se promedia. Así los percentiles extremos
    no cambian sólo porque un periodo tenga más datos que otro.
    `funciones`: {nombre: f(índices de presencias) -> número}.
    """
    grupos = [np.nonzero((anio >= a) & (anio <= b))[0] for a, b in ventanas]
    n = min(len(g) for g in grupos)
    series = {k: np.zeros(len(ventanas)) for k in funciones}
    for w, g in enumerate(grupos):
        acumulado = {k: 0.0 for k in funciones}
        for _ in range(REMUESTREOS):
            sub = g if len(g) == n else rng.choice(g, n, replace=False)
            for k, f in funciones.items():
                acumulado[k] += f(sub)
        for k in funciones:
            series[k][w] = acumulado[k] / REMUESTREOS
    return series, n


# --------------------------------------------------------------------------- cálculo

def calcular(nombre: str, avist: np.ndarray, extra: dict | None = None) -> dict:
    """Todo el cálculo de una especie a partir de sus avistamientos (n, 3) [lat, lon, año]."""
    meta = habitat.leer_meta()
    if not meta or len(meta["anios"]["sst_winter_min"]) < tendencias.MINIMO:
        raise ErrorEspecie("No hay almacén de hábitat: ejecuta antes `python habitat.py`")
    anios = [a for a in meta["anios"]["sst_winter_min"]
             if a in meta["anios"]["sst_summer_max"] and a in meta["anios"]["mhw_months"]]
    anios_chl = [a for a in meta["anios"]["chl"] if a in anios]
    mar = habitat.mar()
    rng = np.random.default_rng(12345)

    # 1. Presencias y valor de cada variable en su celda y año.
    pr = presencias(avist, mar, (anios[0], anios[-1]))
    valores = {v: habitat.valores_en(v, pr["anio"], pr["i"], pr["j"]) for v in habitat.VARIABLES}
    ok = np.logical_and.reduce([np.isfinite(valores[v]) for v in habitat.TEMPERATURA])
    for k in ("anio", "i", "j", "n"):
        pr[k] = pr[k][ok]
    valores = {v: x[ok] for v, x in valores.items()}
    n_pres = int(ok.sum())
    if n_pres < MIN_PRESENCIAS:
        raise ErrorEspecie(f"'{nombre}' tiene {n_pres} presencias con datos desde {anios[0]}: "
                           f"hacen falta al menos {MIN_PRESENCIAS}")
    lat_pres = habitat.LATS[pr["i"]]

    # 2. Perfil: percentiles de todas las presencias y, aparte, año a año.
    muestras, nicho = {}, {}
    for v in habitat.VARIABLES:
        x = np.sort(valores[v][np.isfinite(valores[v])])
        if len(x) >= MIN_PRESENCIAS:
            muestras[v] = x
            nicho[v] = {"n": int(len(x)), **{f"p{p}": round(float(q), 4) for p, q in zip(PERCENTILES, np.percentile(x, PERCENTILES))}}
    por_anio = np.array([(pr["anio"] == a).sum() for a in anios])
    perfil_anual = {"anios": anios, "n": [int(x) for x in por_anio]}
    for v in muestras:
        filas = {p: [] for p in (5, 50, 95)}
        for a, n in zip(anios, por_anio):
            x = valores[v][(pr["anio"] == a) & np.isfinite(valores[v])]
            q = np.percentile(x, [5, 50, 95]) if len(x) >= MIN_ANIO_PERFIL else [np.nan] * 3
            for p, valor in zip((5, 50, 95), q):
                filas[p].append(valor)
        perfil_anual[v] = {f"p{p}": _lista(filas[p], 3) for p in filas}

    # 3. Idoneidad en el Mediterráneo, por celda y año.
    g = habitat.rejilla_region(regiones.MEDITERRANEO)
    filas_m, cols_m = np.nonzero(g["mar"])
    n_celdas = len(filas_m)
    idoneidad = np.full((len(anios), n_celdas), np.inf, dtype="float32")
    limitante = np.full((len(anios), n_celdas), -1, dtype="int8")
    en_celdas = {}
    for k, v in enumerate(habitat.TEMPERATURA):
        en_celdas[v] = habitat.caja(v, anios)[:, filas_m, cols_m]
        s = puntuacion(muestras[v], en_celdas[v], LADO.get(v, "ambos"))
        menor = s < idoneidad
        idoneidad = np.where(menor, s, idoneidad)
        limitante[menor] = k
        idoneidad[np.isnan(s)] = np.nan
    limitante[np.isnan(idoneidad)] = -1
    idoneidad[np.isinf(idoneidad)] = np.nan
    con_chl = None
    if "chl" in muestras and len(anios_chl) >= tendencias.MINIMO:
        k0 = anios.index(anios_chl[0])
        s = puntuacion(muestras["chl"], habitat.caja("chl", anios_chl)[:, filas_m, cols_m], LADO.get("chl", "ambos"))
        con_chl = np.minimum(idoneidad[k0:k0 + len(anios_chl)], s)          # NaN si falta cualquiera

    # 4a. Indicadores del mapa: superficie, centro y límite hacia el polo del hábitat idóneo.
    lat_c = g["lat"][filas_m]
    area_celda = (habitat.PASO * KM_POR_GRADO) ** 2 * np.cos(np.deg2rad(lat_c))
    hay = np.isfinite(idoneidad)
    idonea = hay & (idoneidad >= UMBRAL_IDONEO)
    area = (idonea * area_celda).sum(axis=1)
    area_total = float((hay.any(axis=0) * area_celda).sum())
    centro, limite = np.full(len(anios), np.nan), np.full(len(anios), np.nan)
    for k in range(len(anios)):
        if idonea[k].sum() >= 5:
            centro[k] = np.average(lat_c[idonea[k]], weights=area_celda[idonea[k]])
            limite[k] = _percentiles_ponderados(lat_c[idonea[k]], area_celda[idonea[k]], 95)
    indicadores = {
        "area": {"nombre": "Suitable area in the Mediterranean", "unidad": "km²", "tipo": "modelo", "anios": anios,
                 "serie": _lista(area, 0), "pct_del_mar": _lista(100 * area / area_total, 1),
                 **tendencias.resumen(area, anios, 0)},
        "centro": {"nombre": "Centre of the suitable habitat (latitude)", "unidad": "°N", "tipo": "modelo",
                   "anios": anios, "serie": _lista(centro, 3), **tendencias.resumen(centro, anios, 3)},
        "limite": {"nombre": "Poleward limit of the suitable habitat (latitude)", "unidad": "°N", "tipo": "modelo",
                   "anios": anios, "serie": _lista(limite, 3), **tendencias.resumen(limite, anios, 3)},
    }

    # 4b. Tendencia de la idoneidad en cada celda (FDR sobre las celdas).
    pend, p, _ = tendencias.tendencias(idoneidad, anios)
    tendencia_celdas = {"slope_per_decade": _lista(pend, 4), "p": _lista(np.where(np.isfinite(pend), p, np.nan), 4),
                        "p_adj": _lista(tendencias.fdr(np.where(np.isfinite(pend), p, np.nan)), 4)}

    # 4c. Indicadores observados, por ventanas de años con el mismo número de presencias.
    observado = {"ventanas": None, "motivo": None}
    elegido = elegir_ventanas(pr["anio"], anios[0], anios[-1])
    if elegido is None:
        observado["motivo"] = (f"Fewer than {tendencias.MINIMO} periods with at least {MIN_VENTANA} presences: "
                               "not enough sightings spread over the years.")
    else:
        ventanas, ancho = elegido
        t = np.array([(a + b) / 2 for a, b in ventanas])
        medio = (valores["sst_winter_min"] + valores["sst_summer_max"]) / 2
        series, n_igual = serie_por_ventanas(pr["anio"], ventanas, {
            "frio": lambda s: np.percentile(valores["sst_winter_min"][s], 5),
            "centro": lambda s: np.percentile(medio[s], 50),
            "calor": lambda s: np.percentile(valores["sst_summer_max"][s], 95),
        }, rng)
        # Cuánto se ha calentado el mar en los sitios donde se ha visto la especie (todas sus celdas).
        celdas = np.unique(pr["i"] * habitat.NLON + pr["j"])
        ci, cj = celdas // habitat.NLON, celdas % habitat.NLON
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            sitio = {v: np.array([np.nanmean(habitat.anual(v, a)[ci, cj]) for a in anios])
                     for v in ("sst_winter_min", "sst_summer_max")}
        sitio["medio"] = (sitio["sst_winter_min"] + sitio["sst_summer_max"]) / 2
        nombres = {"frio": ("Cold limit: 5th percentile of the coldest month where it is seen", "sst_winter_min"),
                   "centro": ("Centre: median of the mid-year temperature where it is seen", "medio"),
                   "calor": ("Warm limit: 95th percentile of the hottest month where it is seen", "sst_summer_max")}
        tolerancia = {}
        for k, (texto, base) in nombres.items():
            r = tendencias.resumen(series[k], t, 2)
            mar_b, mar_p = tendencias.tendencia(sitio[base], anios)
            r.update(nombre=texto, unidad="°C", tipo="observado", anios=[float(x) for x in t], serie=_lista(series[k], 3),
                     calentamiento_decada=round(mar_b, 3), calentamiento_p=round(mar_p, 5),
                     razon=None if r["pendiente_decada"] is None or abs(mar_b) < 0.02
                     else round(r["pendiente_decada"] / mar_b, 2))
            tolerancia[k] = r
        observado.update(ventanas=[list(v) for v in ventanas], anios_por_ventana=ancho, presencias_por_ventana=n_igual,
                         celdas_distintas=int(len(celdas)))
        indicadores.update({"tolerancia_" + k: v for k, v in tolerancia.items()})
        # Límite hacia el polo observado, por hemisferio (latitud absoluta, percentil 95).
        for hemisferio, de, texto in (("norte", lat_pres > 0, "Northern"), ("sur", lat_pres < 0, "Southern")):
            sub = np.nonzero(de)[0]
            el = elegir_ventanas(pr["anio"][sub], anios[0], anios[-1])
            if el is None:
                continue
            vent, _ = el
            serie, n_h = serie_por_ventanas(pr["anio"][sub], vent,
                                            {"lat": lambda s: np.percentile(np.abs(lat_pres[sub][s]), 95)}, rng)
            th = np.array([(a + b) / 2 for a, b in vent])
            indicadores["limite_observado_" + hemisferio] = {
                "nombre": f"Observed poleward limit, {texto} Hemisphere (95th percentile of latitude)",
                "unidad": "°" + ("N" if hemisferio == "norte" else "S"), "tipo": "observado",
                "anios": [float(x) for x in th], "serie": _lista(serie["lat"], 3), "presencias_por_ventana": n_h,
                **tendencias.resumen(serie["lat"], th, 3)}
    tendencias.ajustar(list(indicadores.values()))       # FDR sobre todos los indicadores de la especie

    # 5. Lo demás que dibuja la web.
    no_idonea = hay & ~idonea
    cuenta_lim = {v: int((limitante[no_idonea] == k).sum()) for k, v in enumerate(habitat.TEMPERATURA)}
    total_lim = max(1, sum(cuenta_lim.values()))
    # Presencias dentro de la caja del Mediterráneo (para pintarlas sobre el mapa).
    fila = g["ny"] - 1 - (pr["i"] - g["i0"])
    col = pr["j"] - g["j0"]
    dentro = (fila >= 0) & (fila < g["ny"]) & (col >= 0) & (col < g["nx"])
    # Dónde se ha visto en el mundo, agrupado a celdas de 2° (mapa pequeño de contexto).
    mi, mj = np.floor((lat_pres + 90) / 2).astype(int), np.floor((habitat.LONS[pr["j"]] + 180) / 2).astype(int)
    clave, cuenta = np.unique(mi * 180 + mj, return_counts=True)

    ents = lambda a: [None if x < 0 else int(x) for x in np.asarray(a).ravel()]
    pct = lambda a: [[None if not np.isfinite(x) else int(round(100 * x)) for x in fila_] for fila_ in a]
    return {
        **(extra or {}),
        "nombre": nombre, "calculado": _ahora(), "version_habitat": meta["version"], "version_calculo": VERSION_CALCULO,
        "registros": pr["registros"], "registros_sin_anio": pr["sin_anio"], "registros_en_tierra": pr["en_tierra"],
        "presencias": n_pres, "presencias_mediterraneo": int(dentro.sum()),
        "latitud_presencias": [round(float(lat_pres.min()), 2), round(float(lat_pres.max()), 2)],
        "umbral_idoneo": UMBRAL_IDONEO, "metodo": tendencias.METODO,
        "region": {"name": regiones.MEDITERRANEO["name"], "lon_west": float(regiones.MEDITERRANEO["lon"][0]),
                   "lat_north": float(regiones.MEDITERRANEO["lat"][1]), "cell_deg": habitat.PASO,
                   "nx": g["nx"], "ny": g["ny"]},
        "ocean_index": [int(a) * g["nx"] + int(b) for a, b in zip(filas_m, cols_m)],
        "anios": anios,
        "variables": [{"id": v, **{k: habitat.VARIABLES[v][k] for k in ("name", "unit", "decimals", "desde")},
                       "en_indice": v in habitat.TEMPERATURA,
                       "lado": LADO.get(v, "ambos")} for v in habitat.VARIABLES if v in nicho],
        "nicho": nicho, "perfil_anual": perfil_anual,
        "idoneidad": pct(idoneidad),
        "limitante": [ents(f) for f in limitante], "limitantes": habitat.TEMPERATURA,
        "idoneidad_chl": None if con_chl is None else {"anios": anios_chl, "valores": pct(con_chl)},
        "tendencia_celdas": tendencia_celdas,
        "indicadores": indicadores, "observado": observado,
        "factores_limitantes_pct": {v: round(100 * n / total_lim, 1) for v, n in cuenta_lim.items() if n},
        "avistamientos": {"celda": [int(x) for x in (fila * g["nx"] + col)[dentro]],
                          "anio": [int(x) for x in pr["anio"][dentro]], "n": [int(x) for x in pr["n"][dentro]]},
        "mundo": [[int(c // 180) * 2 - 89, int(c % 180) * 2 - 179, int(n)] for c, n in zip(clave, cuenta)],
    }


# --------------------------------------------------------------------------- guardar, lista, a demanda

def _resumen(d: dict) -> dict:
    area = d["indicadores"]["area"]
    return {k: d.get(k) for k in ("nombre", "nombre_comun", "principal", "registros", "presencias", "calculado",
                                 "version_habitat", "version_calculo", "obis_descargado", "aphia_id")} | {
        "area_pendiente_decada": area["pendiente_decada"], "area_significativa": area["significativa"]}


def guardar(d: dict) -> None:
    _escribir(ruta(d["nombre"]), json.dumps(d, separators=(",", ":")))
    _escribir(ruta(d["nombre"]).with_suffix(".resumen.json"), json.dumps(_resumen(d)))


def leer_resumen(nombre: str) -> dict | None:
    r = ruta(nombre).with_suffix(".resumen.json")
    return json.loads(r.read_text()) if r.exists() and ruta(nombre).exists() else None


def listar() -> list[dict]:
    if not DIR.is_dir():
        return []
    lista = [json.loads(r.read_text()) for r in sorted(DIR.glob("*.resumen.json"))]
    return sorted(lista, key=lambda e: (not e.get("principal"), e["nombre"]))


def esta_al_dia(nombre: str) -> bool:
    r, meta = leer_resumen(nombre), habitat.leer_meta()
    return bool(r and meta and r.get("version_habitat") == meta["version"]
                and r.get("version_calculo") == VERSION_CALCULO)


def _obis_reciente(nombre: str) -> bool:
    """Los avistamientos guardados se reutilizan hasta DIAS_REFRESCO_OBIS días."""
    descargado = (leer_resumen(nombre) or {}).get("obis_descargado")
    return bool(descargado and ruta(nombre).with_suffix(".obis.npy").exists()
                and datetime.fromisoformat(descargado) > datetime.now(timezone.utc) - timedelta(days=DIAS_REFRESCO_OBIS))


def calcular_y_guardar(nombre: str, refrescar_obis: bool = False) -> dict:
    """Descarga los avistamientos (o reutiliza los guardados si son recientes), calcula y guarda."""
    import especies as obis                     # el cliente de OBIS ya existente
    if not habitat.leer_meta():                 # antes de descargar nada de OBIS
        raise habitat.ErrorHabitat("No hay almacén de hábitat: ejecuta antes `python habitat.py`")
    ruta_obis = ruta(nombre).with_suffix(".obis.npy")
    anterior = leer_resumen(nombre) or {}
    descargado = anterior.get("obis_descargado")
    if _obis_reciente(nombre) and not refrescar_obis:
        avist, extra = np.load(ruta_obis), {"aphia_id": anterior.get("aphia_id"), "obis_descargado": descargado}
    else:
        try:
            taxon = obis._taxon(nombre)
        except obis.ErrorEspecie as e:
            raise ErrorEspecie(str(e))
        avist = obis._descargar_avistamientos(taxon["scientificName"])
        extra = {"aphia_id": taxon.get("taxonID"), "obis_descargado": _ahora()}
    extra.update(nombre_comun=obis.ESPECIES_PRINCIPALES.get(nombre), principal=nombre in obis.ESPECIES_PRINCIPALES)
    d = calcular(nombre, avist, extra)
    DIR.mkdir(parents=True, exist_ok=True)
    np.save(ruta_obis, avist)
    guardar(d)
    log.info("%s: %d registros, %d presencias, superficie idónea %s km²/década (p ajustada %s)", nombre,
             d["registros"], d["presencias"], d["indicadores"]["area"]["pendiente_decada"], d["indicadores"]["area"]["p_adj"])
    return d


_cerrojos: dict[str, threading.Lock] = defaultdict(threading.Lock)
_cerrojo_calculo = threading.Lock()     # un cálculo a la vez por proceso


@contextmanager
def _cerrojo(nombre: str):
    """Exclusión entre hilos y entre procesos (varios workers de uvicorn) para una especie."""
    DIR.mkdir(parents=True, exist_ok=True)
    ruta_lock = ruta(nombre).with_suffix(".lock")
    with _cerrojos[nombre], open(ruta_lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if not ruta(nombre).exists():
                ruta_lock.unlink(missing_ok=True)
            fcntl.flock(f, fcntl.LOCK_UN)


def asegurar(nombre: str) -> Path:
    """Ruta del JSON de la especie; la calcula si no está o si el almacén de hábitat ha cambiado."""
    if not esta_al_dia(nombre):
        with _cerrojo(nombre), _cerrojo_calculo:
            if not esta_al_dia(nombre):
                calcular_y_guardar(nombre)
    return ruta(nombre)


def calcular_principales() -> None:
    import especies as obis
    for nombre in obis.ESPECIES_PRINCIPALES:
        if esta_al_dia(nombre) and _obis_reciente(nombre):
            continue                            # nada ha cambiado desde el último cálculo
        try:
            with _cerrojo(nombre):
                calcular_y_guardar(nombre)
        except Exception as e:  # una especie que falla no detiene las demás
            log.error("%s: %s", nombre, e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    if sys.argv[1:]:
        for nombre in sys.argv[1:]:
            with _cerrojo(nombre):
                calcular_y_guardar(nombre, refrescar_obis=True)
    else:
        calcular_principales()


if __name__ == "__main__":
    main()
