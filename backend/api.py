"""
API web (FastAPI) que sirve los datos guardados por el recolector. No consulta
a la NASA: sólo lee el almacén Zarr (con xarray), así que responde en milisegundos.

    uvicorn api:app --host 0.0.0.0 --port 8000
    Documentación interactiva: http://localhost:8000/docs

Endpoints:
    GET /api/variables                       Catálogo de variables.
    GET /api/datos?lat=&lon=&desde=&hasta=   Series en el punto de la rejilla más cercano.
    GET /api/mapa?variable=&fecha=           Valores de todos los puntos en una fecha (para un mapa).
    GET /api/estado                          Última fecha guardada de cada variable.

    GET /api/especies                        Especies ya calculadas (las principales siempre).
    GET /api/especies/buscar?q=              Autocompletado de nombres científicos (OBIS).
    GET /api/especies/{nombre}               Nicho y resumen; si no está calculada, se calcula ahora.
    GET /api/especies/{nombre}/mapa?mes=     Idoneidad por punto y avistamientos, para un mapa.

La web de ejemplo (carpeta web/) se sirve en / y llama a estos endpoints con fetch().
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import almacen
import especies
from datos_mediterraneo import VARIABLES, media_serie
from recolector import VARIABLES_RECOLECTADAS

app = FastAPI(title="Estado del Mediterráneo", version="1.0")
# Ajustar allow_origins al dominio de la web en producción.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

GIOVANNI = {v for v in VARIABLES if VARIABLES[v]["fuentes"][-1]["tipo"] == "giovanni"}


def _fuente(variable: str) -> dict:
    return VARIABLES[variable]["fuentes"][-1]


def _ambiente():
    ds = almacen.abrir_ambiente()
    if ds is None:
        raise HTTPException(503, "Todavía no hay datos: ejecuta el recolector")
    return ds


@app.get("/api/variables")
def variables():
    return {v: {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                "dataset": _fuente(v)["dataset"],
                "fuente": "NASA Giovanni" if v in GIOVANNI else "NOAA CoastWatch ERDDAP",
                "resolucion_grados": 1.0 if v in GIOVANNI else almacen.PASO}
            for v in VARIABLES_RECOLECTADAS}


@app.get("/api/datos")
def datos(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
          desde: date | None = None, hasta: date | None = None,
          variables: list[str] = Query(default=VARIABLES_RECOLECTADAS)):
    """Misma estructura que `obtener_datos_mediterraneo`, más la celda usada para cada variable."""
    hasta = hasta or date.today()
    desde = desde or hasta - timedelta(days=90)
    desconocidas = set(variables) - set(VARIABLES_RECOLECTADAS)
    if desconocidas:
        raise HTTPException(400, f"Variables desconocidas: {sorted(desconocidas)}")

    ds = _ambiente()
    mar = ds["mar"].values
    resultado = {}
    for v in variables:
        entrada = {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                   "dataset": _fuente(v)["dataset"], "punto": None, "serie": [],
                   "media": None, "ultimo": None, "error": None}
        celda = almacen.celda_mas_cercana(mar, lat, lon, solo_1_grado=v in GIOVANNI)
        if celda is None:
            entrada["error"] = "No hay celdas de mar cerca de esa coordenada"
        elif v not in ds:
            entrada["error"] = "Variable todavía sin datos"
        else:
            la, lo = float(almacen.LATS[celda[0]]), float(almacen.LONS[celda[1]])
            entrada["punto"] = {"lat": la, "lon": lo, "distancia_km": round(float(almacen.distancia_km(lat, lon, la, lo)), 1)}
            serie = ds[v].isel(lat=celda[0], lon=celda[1]).sel(time=slice(desde.isoformat(), hasta.isoformat())).load()
            serie = serie[serie.notnull()]
            entrada["serie"] = [{"fecha": str(t)[:10], "valor": round(float(x), 6)}
                                for t, x in zip(serie.time.values, serie.values)]
            if entrada["serie"]:
                entrada["media"] = media_serie(v, [p["valor"] for p in entrada["serie"]])
                entrada["ultimo"] = entrada["serie"][-1]
        resultado[v] = entrada
    return {"lat": lat, "lon": lon, "fecha_inicio": desde.isoformat(),
            "fecha_fin": hasta.isoformat(), "variables": resultado}


@app.get("/api/mapa")
def mapa(variable: str, fecha: date | None = None):
    """Valor de cada celda en `fecha`, o en la fecha más reciente con datos si no se indica."""
    if variable not in VARIABLES_RECOLECTADAS:
        raise HTTPException(400, f"Variable desconocida: {variable}")
    ds = _ambiente()
    if variable not in ds or not ds[variable].attrs.get("hasta"):
        return {"variable": variable, "fecha": None, "puntos": []}
    fecha = fecha or date.fromisoformat(ds[variable].attrs["hasta"])
    valores = ds[variable].sel(time=fecha.isoformat()).values
    ii, jj = np.nonzero(~np.isnan(valores))
    return {"variable": variable, "unidades": VARIABLES[variable]["unidades"], "fecha": fecha.isoformat(),
            "puntos": [[float(almacen.LATS[i]), float(almacen.LONS[j]), round(float(valores[i, j]), 6)]
                       for i, j in zip(ii, jj)]}


@app.get("/api/estado")
def estado():
    ds = almacen.abrir_ambiente()
    if ds is None:
        return {"celdas_mar": 0, "variables": {}}
    return {"celdas_mar": int(ds["mar"].values.sum()),
            "variables": {v: {"desde": ds[v].attrs.get("desde"), "hasta": ds[v].attrs.get("hasta")}
                          for v in VARIABLES_RECOLECTADAS if v in ds}}


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
    nicho = ds["nicho"].values
    datos["nicho"] = {str(v): {"n": int(n), **{f"p{p}": round(float(x), 6) for p, x in zip(ds.percentil.values, fila)},
                               "unidades": VARIABLES[str(v)]["unidades"]}
                      for v, n, fila in zip(ds.variable.values, ds["nicho_n"].values, nicho)}
    return datos


@app.get("/api/especies/{nombre}/mapa")
def especie_mapa(nombre: str, mes: int | None = Query(None, ge=1, le=12)):
    """`mes` 1-12; por defecto, el mes actual."""
    nombre = _nombre_especie(nombre)
    mes = mes or date.today().month
    ds = _asegurar(nombre)
    idoneidad = ds["idoneidad"].sel(mes=mes).values
    limitante = ds["limitante"].sel(mes=mes).values
    codigos = ds["limitante"].attrs["codigos"]
    registros = ds["registros"].sum("mes_registro").values
    ii, jj = np.nonzero(~np.isnan(idoneidad))
    ri, rj = np.nonzero(registros)
    return {"especie": nombre, "mes": mes, "umbral_idoneo": especies.UMBRAL_IDONEO,
            "puntos": [[float(almacen.LATS[i]), float(almacen.LONS[j]), round(float(idoneidad[i, j]), 4),
                        codigos[limitante[i, j]] if limitante[i, j] >= 0 else None] for i, j in zip(ii, jj)],
            "avistamientos": [[float(almacen.LATS[i]), float(almacen.LONS[j]), int(registros[i, j])]
                              for i, j in zip(ri, rj)]}


# La web de ejemplo. Se monta al final para que no tape las rutas /api.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
