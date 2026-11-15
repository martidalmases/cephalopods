"""
API web (FastAPI) que sirve los datos guardados por el recolector. No consulta
a la NASA: sólo lee el almacén Zarr (con xarray), así que responde en milisegundos.

    uvicorn api:app --host 0.0.0.0 --port 8000
    Documentación interactiva: http://localhost:8000/docs

Endpoints:
    GET /api/variables                       Catálogo de variables.
    GET /api/datos?lat=&lon=&desde=&hasta=   Series MENSUALES en la celda de mar más cercana (1980-hoy).
    GET /api/mapa?variable=&mes=AAAA-MM      Valores de todas las celdas en un mes (para un mapa).
                                             lat_min/lat_max/lon_min/lon_max recortan una zona.
    GET /api/estado                          Primer y último mes guardado de cada variable.

    GET /api/especies                        Especies ya calculadas (las principales siempre).
    GET /api/especies/buscar?q=              Autocompletado de nombres científicos (OBIS).
    GET /api/especies/{nombre}               Nicho y resumen anual; si no está calculada, se calcula ahora.
    GET /api/especies/{nombre}/mapa?anio=    Idoneidad por celda en un año y avistamientos, para un mapa.

    GET /api/trends                          Conjuntos para la pantalla Trends (región + años).
    GET /api/trends/{conjunto}/{fichero}     meta.json y <variable>.json en el formato de la web.

La web de ejemplo (carpeta web/) se sirve en / y llama a estos endpoints con fetch().
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import almacen
import especies
import exportar_trends
from datos_mediterraneo import VARIABLES, media_serie
import recolector
from recolector import FUENTES, VARIABLES_RECOLECTADAS

app = FastAPI(title="Estado del Mediterráneo", version="1.0")
# Ajustar allow_origins al dominio de la web en producción.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
# Los mapas mundiales son JSON grandes y muy repetitivos: comprimidos ocupan ~5 veces menos.
app.add_middleware(GZipMiddleware, minimum_size=2000)


class Zona:
    """Recorte opcional de los mapas (por defecto, todo el planeta)."""
    def __init__(self, lat_min: float = Query(-90, ge=-90, le=90), lat_max: float = Query(90, ge=-90, le=90),
                 lon_min: float = Query(-180, ge=-180, le=180), lon_max: float = Query(180, ge=-180, le=180)):
        self.caja = (lat_min, lat_max, lon_min, lon_max)

    def celdas(self, mascara: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Celdas (filas, columnas) de `mascara` dentro de la zona, para una rejilla con esos centros."""
        lat_min, lat_max, lon_min, lon_max = self.caja
        filas = (lats >= lat_min) & (lats <= lat_max)
        columnas = (lons >= lon_min) & (lons <= lon_max)
        return np.nonzero(mascara & filas[:, None] & columnas[None, :])


def _mes(texto: str) -> date:
    try:
        return date(int(texto[:4]), int(texto[5:7]), 1)
    except (ValueError, IndexError):
        raise HTTPException(400, f"Mes no válido: {texto!r} (formato AAAA-MM)")


def _ambiente(variable: str):
    """(rejilla, Dataset) del almacén mensual donde está la variable (1° o 0,5°)."""
    r = almacen.rejilla_de(variable) or almacen.BASE
    ds = almacen.abrir_ambiente(r)
    if ds is None:
        raise HTTPException(503, "Todavía no hay datos: ejecuta el recolector")
    return r, ds


@app.get("/api/variables")
def variables():
    return {v: {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                "fuente": FUENTES[v]["fuente"], "desde": FUENTES[v]["desde"], "paso": "mensual",
                "resolucion_grados": recolector.rejilla(v).paso}
            for v in VARIABLES_RECOLECTADAS}


@app.get("/api/datos")
def datos(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
          desde: str | None = Query(None, description="AAAA-MM; por defecto, 1980-01"),
          hasta: str | None = Query(None, description="AAAA-MM; por defecto, el mes actual"),
          variables: list[str] = Query(default=VARIABLES_RECOLECTADAS)):
    """Serie mensual de cada variable en la celda de mar más cercana, más la celda usada."""
    hasta_m = _mes(hasta) if hasta else date.today().replace(day=1)
    desde_m = _mes(desde) if desde else almacen.ORIGEN
    desconocidas = set(variables) - set(VARIABLES_RECOLECTADAS)
    if desconocidas:
        raise HTTPException(400, f"Variables desconocidas: {sorted(desconocidas)}")

    resultado = {}
    for v in variables:
        entrada = {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                   "fuente": FUENTES[v]["fuente"], "punto": None, "serie": [],
                   "media": None, "ultimo": None, "error": None}
        r, ds = _ambiente(v)
        celda = almacen.celda_mas_cercana(ds["mar"].values, lat, lon, r=r)
        if celda is None:
            entrada["error"] = "No hay celdas de mar cerca de esa coordenada"
        elif v not in ds:
            entrada["error"] = "Variable todavía sin datos"
        else:
            la, lo = float(r.lats[celda[0]]), float(r.lons[celda[1]])
            entrada["resolucion_grados"] = r.paso
            entrada["punto"] = {"lat": la, "lon": lo, "distancia_km": round(float(almacen.distancia_km(lat, lon, la, lo)), 1)}
            serie = ds[v].isel(lat=celda[0], lon=celda[1]).sel(time=slice(desde_m.isoformat(), hasta_m.isoformat())).load()
            serie = serie[serie.notnull()]
            entrada["serie"] = [{"mes": str(t)[:7], "valor": round(float(x), 6)}
                                for t, x in zip(serie.time.values, serie.values)]
            if entrada["serie"]:
                entrada["media"] = media_serie(v, [p["valor"] for p in entrada["serie"]])
                entrada["ultimo"] = entrada["serie"][-1]
        resultado[v] = entrada
    return {"lat": lat, "lon": lon, "desde": desde_m.isoformat()[:7], "hasta": hasta_m.isoformat()[:7],
            "paso": "mensual", "variables": resultado}


@app.get("/api/mapa")
def mapa(variable: str, mes: str | None = Query(None, description="AAAA-MM; por defecto, el último con datos"),
         zona: Zona = Depends()):
    """Media mensual de cada celda en `mes`."""
    if variable not in VARIABLES_RECOLECTADAS:
        raise HTTPException(400, f"Variable desconocida: {variable}")
    r, ds = _ambiente(variable)
    if variable not in ds or not ds[variable].attrs.get("hasta"):
        return {"variable": variable, "mes": None, "puntos": []}
    m = _mes(mes or ds[variable].attrs["hasta"])
    valores = ds[variable].sel(time=m.isoformat()).values
    ii, jj = zona.celdas(~np.isnan(valores), r.lats, r.lons)
    return {"variable": variable, "unidades": VARIABLES[variable]["unidades"], "mes": m.isoformat()[:7],
            "paso_grados": r.paso,
            "puntos": [[float(r.lats[i]), float(r.lons[j]), float(f"{valores[i, j]:.5g}")]
                       for i, j in zip(ii, jj)]}


@app.get("/api/estado")
def estado():
    salida = {"rejillas": {}, "variables": {}}
    for r in almacen.REJILLAS:
        g = almacen.abrir_grupo(r)
        if g is None:
            continue
        salida["rejillas"][r.nombre] = {"paso_grados": r.paso, "celdas_mar": int(g["mar"][:].sum())}
        for v in VARIABLES_RECOLECTADAS:
            if v in g:
                salida["variables"][v] = {"desde": g[v].attrs.get("desde"), "hasta": g[v].attrs.get("hasta"),
                                          "resolucion_grados": r.paso}
    return salida


# --------------------------------------------------------------------------- especies

def _nombre_especie(nombre: str) -> str:
    return " ".join(nombre.split()).capitalize()  # "octopus  VULGARIS" -> "Octopus vulgaris"


@app.get("/api/especies")
def lista_especies():
    return especies.listar_especies()


@app.get("/api/especies/buscar")
def buscar_especies(q: str = Query(..., min_length=3)):
    try:
        return especies.buscar_especies(q)
    except requests.RequestException as e:
        raise HTTPException(502, f"OBIS no responde: {e}")


def _asegurar(nombre: str):
    """Las principales ya están calculadas; el resto se calcula en la primera petición (unos segundos)."""
    try:
        especies.asegurar_especie(nombre)
    except especies.ErrorEspecie as e:
        raise HTTPException(404, str(e))
    except requests.RequestException as e:
        raise HTTPException(502, f"OBIS no responde: {e}")
    return especies.leer_especie(nombre)


@app.get("/api/especies/{nombre}")
def especie(nombre: str):
    ds = _asegurar(_nombre_especie(nombre))
    datos = {k: ds.attrs.get(k) for k in ("nombre", "nombre_comun", "aphia_id", "principal", "registros",
                                         "registros_usados", "obis_descargado", "calculado", "resumen")}
    datos["anios"] = [int(a) for a in ds.anio.values]
    nicho = ds["nicho"].values
    datos["nicho"] = {str(v): {"n": int(n), **{f"p{p}": round(float(x), 6) for p, x in zip(ds.percentil.values, fila)},
                               "unidades": VARIABLES[str(v)]["unidades"]}
                      for v, n, fila in zip(ds.variable.values, ds["nicho_n"].values, nicho)}
    return datos


@app.get("/api/especies/{nombre}/mapa")
def especie_mapa(nombre: str, anio: int | None = Query(None, description="por defecto, el último año del periodo"),
                 zona: Zona = Depends()):
    """
    Idoneidad de cada celda en `anio` y avistamientos.
    puntos: [lat, lon, idoneidad, índice del factor limitante en `limitantes` (-1 si no hay)].
    avistamientos: [lat, lon, de todos los años, de `anio`].
    """
    nombre = _nombre_especie(nombre)
    ds = _asegurar(nombre)
    anios = [int(a) for a in ds.anio.values]
    anio = anio or anios[-1]
    if anio not in anios:
        raise HTTPException(404, f"Sin índice para {anio}: el periodo es {anios[0]}-{anios[-1]}")
    idoneidad = ds["idoneidad"].sel(anio=anio).values
    limitante = ds["limitante"].sel(anio=anio).values
    total = ds["registros"].sum("anio_registro").values
    del_anio = (ds["registros"].sel(anio_registro=anio).values if anio in ds.anio_registro.values
                else np.zeros_like(total))
    lats, lons = ds.lat.values, ds.lon.values                      # rejilla de la especie (0,5°)
    ii, jj = zona.celdas(~np.isnan(idoneidad), lats, lons)
    ri, rj = zona.celdas(total > 0, lats, lons)
    return {"especie": nombre, "anio": anio, "umbral_idoneo": especies.UMBRAL_IDONEO,
            "paso_grados": float(round(lats[1] - lats[0], 4)),
            "limitantes": list(ds["limitante"].attrs["codigos"]),
            "puntos": [[float(lats[i]), float(lons[j]), round(float(idoneidad[i, j]), 3),
                        int(limitante[i, j])] for i, j in zip(ii, jj)],
            "avistamientos": [[float(lats[i]), float(lons[j]), int(total[i, j]), int(del_anio[i, j])]
                              for i, j in zip(ri, rj)]}


# --------------------------------------------------------------------------- trends (web)

_NOMBRE_SEGURO = re.compile(r"^[a-z0-9_-]+(\.json)?$")


@app.get("/api/trends")
def trends_conjuntos():
    indice = exportar_trends.DIR_TRENDS / "index.json"
    if not indice.exists():
        raise HTTPException(503, "Todavía no se han exportado los datos para Trends")
    return json.loads(indice.read_text())


@app.get("/api/trends/{conjunto}/{fichero}")
def trends_fichero(conjunto: str, fichero: str):
    """Ficheros estáticos generados por exportar_trends.py (sólo nombres simples: nada de rutas)."""
    if not (_NOMBRE_SEGURO.match(conjunto) and _NOMBRE_SEGURO.match(fichero) and fichero.endswith(".json")):
        raise HTTPException(400, "Nombre no válido")
    ruta = exportar_trends.DIR_TRENDS / conjunto / fichero
    if not ruta.is_file():
        raise HTTPException(404, f"No existe {conjunto}/{fichero}")
    return FileResponse(ruta, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})


# La web de ejemplo. Se monta al final para que no tape las rutas /api.
if (Path(__file__).parent / "web").is_dir():
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
