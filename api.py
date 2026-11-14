"""
API web (FastAPI) que sirve los datos guardados por el recolector. No consulta
a la NASA: sólo lee la base de datos, así que responde en milisegundos.

    uvicorn api:app --host 0.0.0.0 --port 8000
    Documentación interactiva: http://localhost:8000/docs

Endpoints:
    GET /api/variables                       Catálogo de variables.
    GET /api/datos?lat=&lon=&desde=&hasta=   Series en el punto de la rejilla más cercano.
    GET /api/mapa?variable=&fecha=           Valores de todos los puntos en una fecha (para un mapa).
    GET /api/estado                          Última fecha guardada de cada variable.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import bd
from datos_mediterraneo import VARIABLES
from recolector import VARIABLES_RECOLECTADAS

app = FastAPI(title="Estado del Mediterráneo", version="1.0")
# Ajustar allow_origins al dominio de la web en producción.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

GIOVANNI = {v for v in VARIABLES if VARIABLES[v]["fuentes"][-1]["tipo"] == "giovanni"}


def _fuente(variable: str) -> dict:
    return VARIABLES[variable]["fuentes"][-1]


@app.get("/api/variables")
def variables():
    return {v: {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                "dataset": _fuente(v)["dataset"],
                "fuente": "NASA Giovanni" if v in GIOVANNI else "NOAA CoastWatch ERDDAP",
                "resolucion_grados": 1.0 if v in GIOVANNI else bd.PASO_REJILLA}
            for v in VARIABLES_RECOLECTADAS}


@app.get("/api/datos")
def datos(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
          desde: date | None = None, hasta: date | None = None,
          variables: list[str] = Query(default=VARIABLES_RECOLECTADAS)):
    """Misma estructura que `obtener_datos_mediterraneo`, más el punto usado para cada variable."""
    hasta = hasta or date.today()
    desde = desde or hasta - timedelta(days=90)
    desconocidas = set(variables) - set(VARIABLES_RECOLECTADAS)
    if desconocidas:
        raise HTTPException(400, f"Variables desconocidas: {sorted(desconocidas)}")

    con = bd.conectar()
    try:
        resultado = {}
        for v in variables:
            p = bd.punto_mas_cercano(con, lat, lon, solo_giovanni=v in GIOVANNI)
            entrada = {"nombre": VARIABLES[v]["nombre"], "unidades": VARIABLES[v]["unidades"],
                       "dataset": _fuente(v)["dataset"], "punto": None, "serie": [],
                       "media": None, "ultimo": None, "error": None}
            if p is None:
                entrada["error"] = "No hay puntos de mar cerca de esa coordenada"
            else:
                entrada["punto"] = {"lat": p["lat"], "lon": p["lon"],
                                    "distancia_km": round(bd.distancia_km(lat, lon, p["lat"], p["lon"]), 1)}
                filas = con.execute(
                    "SELECT fecha, valor FROM medicion WHERE punto_id = ? AND variable = ? "
                    "AND fecha BETWEEN ? AND ? ORDER BY fecha",
                    (p["id"], v, desde.isoformat(), hasta.isoformat())).fetchall()
                entrada["serie"] = [{"fecha": f["fecha"], "valor": f["valor"]} for f in filas]
                if filas:
                    entrada["media"] = round(mean(f["valor"] for f in filas), 6)
                    entrada["ultimo"] = entrada["serie"][-1]
            resultado[v] = entrada
    finally:
        con.close()
    return {"lat": lat, "lon": lon, "fecha_inicio": desde.isoformat(),
            "fecha_fin": hasta.isoformat(), "variables": resultado}


@app.get("/api/mapa")
def mapa(variable: str, fecha: date | None = None):
    """Valor de cada punto en `fecha`, o en la fecha más reciente con datos si no se indica."""
    if variable not in VARIABLES_RECOLECTADAS:
        raise HTTPException(400, f"Variable desconocida: {variable}")
    con = bd.conectar()
    try:
        if fecha is None:
            ultima = con.execute("SELECT MAX(fecha) FROM medicion WHERE variable = ?", (variable,)).fetchone()[0]
            if ultima is None:
                return {"variable": variable, "fecha": None, "puntos": []}
            fecha = date.fromisoformat(ultima)
        filas = con.execute(
            "SELECT p.lat, p.lon, m.valor FROM medicion m JOIN punto p ON p.id = m.punto_id "
            "WHERE m.variable = ? AND m.fecha = ?", (variable, fecha.isoformat())).fetchall()
    finally:
        con.close()
    return {"variable": variable, "unidades": VARIABLES[variable]["unidades"], "fecha": fecha.isoformat(),
            "puntos": [[f["lat"], f["lon"], f["valor"]] for f in filas]}


@app.get("/api/estado")
def estado():
    con = bd.conectar()
    try:
        filas = con.execute("SELECT variable, MIN(fecha), MAX(fecha), COUNT(*) FROM medicion GROUP BY variable").fetchall()
        puntos = con.execute("SELECT COUNT(*) FROM punto").fetchone()[0]
    finally:
        con.close()
    return {"puntos": puntos,
            "variables": {f[0]: {"desde": f[1], "hasta": f[2], "valores": f[3]} for f in filas}}
