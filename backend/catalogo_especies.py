"""
Catálogo local de todas las especies marinas con registros en OBIS, para que el buscador de
la web no tenga que consultar OBIS en cada tecla.

    DATOS_DIR/catalogo_especies.json.gz
        {"actualizado": "...", "especies": [[nombre, registros, familia, clase, aphia_id], ...]}

Descarga: la lista de comprobación de OBIS (`/v3/checklist`) da todos los taxones ordenados
de más a menos registros, pero no deja paginar más allá de la posición 100.000 (y hay unos
200.000). Por eso se pide filo a filo: cada filo cabe en esa ventana (si alguno no cabe, se
parte en clases). Sólo se guardan especies con nombre aceptado.

Búsqueda (en memoria, milisegundos):
  1. Nombres que empiezan por lo escrito («octo», «Octopus v»), los más registrados primero.
  2. Si faltan resultados, nombres con alguna palabra que empieza así («vulgaris»).

Se actualiza al final del recolector si tiene más de DIAS_REFRESCO días, o a mano:

    python catalogo_especies.py
"""

from __future__ import annotations

import bisect
import gzip
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import almacen

log = logging.getLogger("catalogo_especies")

RUTA = almacen.DATOS_DIR / "catalogo_especies.json.gz"
OBIS_URL = "https://api.obis.org/v3/checklist"
VENTANA = 100_000            # OBIS no pagina más allá de esta posición
PAGINA = 10_000
DIAS_REFRESCO = 30


def _get(params: dict) -> dict:
    for intento in range(5):
        try:
            r = requests.get(OBIS_URL, params=params, timeout=300)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(10 * (intento + 1))
    raise RuntimeError(f"OBIS no responde: {params}")


def _total(nombre: str | None) -> int:
    return int(_get({"size": 1, **({"scientificname": nombre} if nombre else {})}).get("total") or 0)


def _todas(nombre: str, total: int) -> list[dict]:
    """Todos los taxones bajo `nombre` (total <= VENTANA), página a página."""
    resultados = []
    for salto in range(0, total, PAGINA):
        resultados += _get({"scientificname": nombre, "size": min(PAGINA, VENTANA - salto), "skip": salto})["results"]
    return resultados


def _grupos() -> dict[str, set[str]]:
    """{filo: sus clases} de los taxones más registrados (las clases, por si algún filo no cabe)."""
    grupos: dict[str, set[str]] = {}
    for salto in range(0, VENTANA, PAGINA):
        for t in _get({"size": PAGINA, "skip": salto})["results"]:
            if t.get("phylum"):
                clases = grupos.setdefault(t["phylum"], set())
                if t.get("class"):
                    clases.add(t["class"])
    return grupos


def actualizar() -> int:
    """Descarga el catálogo completo y lo guarda. Devuelve el número de especies."""
    t0 = time.monotonic()
    grupos = _grupos()
    especies: dict[int | str, list] = {}

    def guardar(taxones: list[dict]) -> None:
        for t in taxones:
            if t.get("taxonRank") == "Species" and t.get("taxonomicStatus", "accepted") == "accepted":
                clave = t.get("taxonID") or t["scientificName"]
                especies[clave] = [t["scientificName"], int(t.get("records") or 0), t.get("family"),
                                   t.get("class"), t.get("taxonID")]

    for filo in sorted(grupos):
        total = _total(filo)
        if total <= VENTANA:
            guardar(_todas(filo, total))
        else:   # filo demasiado grande: clase a clase
            for clase in sorted(grupos[filo]):
                tc = _total(clase)
                if tc and tc <= VENTANA:
                    guardar(_todas(clase, tc))
        log.info("%-20s %7d taxones; %d especies acumuladas", filo, total, len(especies))

    lista = sorted(especies.values(), key=lambda e: e[0].lower())
    datos = {"actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "fuente": "OBIS checklist (api.obis.org/v3/checklist)", "especies": lista}
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUTA.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(datos, f, separators=(",", ":"))
    os.replace(tmp, RUTA)
    log.info("Catálogo: %d especies en %.0f s (%d KB)", len(lista), time.monotonic() - t0, RUTA.stat().st_size // 1024)
    return len(lista)


def caducado() -> bool:
    if not RUTA.exists():
        return True
    edad = datetime.now(timezone.utc) - datetime.fromtimestamp(RUTA.stat().st_mtime, timezone.utc)
    return edad > timedelta(days=DIAS_REFRESCO)


# --------------------------------------------------------------------------- búsqueda

class _Indice:
    """El catálogo en memoria: nombres en minúsculas ordenados, para buscar por prefijo con bisect."""
    def __init__(self, especies: list[list]):
        self.especies = especies
        self.claves = [e[0].lower() for e in especies]


_indice: _Indice | None = None
_mtime = 0.0
_cerrojo = threading.Lock()


def _cargar() -> _Indice | None:
    """Carga (o recarga, si el fichero ha cambiado) el catálogo. None si todavía no existe."""
    global _indice, _mtime
    if not RUTA.exists():
        return None
    mtime = RUTA.stat().st_mtime
    if _indice is None or mtime != _mtime:
        with _cerrojo:
            if _indice is None or mtime != _mtime:
                with gzip.open(RUTA, "rt", encoding="utf-8") as f:
                    _indice = _Indice(json.load(f)["especies"])
                _mtime = mtime
    return _indice


def disponible() -> bool:
    return RUTA.exists()


def buscar(texto: str, limite: int = 15) -> list[dict] | None:
    """Especies cuyo nombre empieza por `texto` (y, si faltan, con una palabra que empieza así)."""
    indice = _cargar()
    if indice is None:
        return None
    q = " ".join(texto.lower().split())
    if not q:
        return []
    i = bisect.bisect_left(indice.claves, q)
    j = bisect.bisect_left(indice.claves, q + "￿")
    primeros = sorted(range(i, j), key=lambda k: -indice.especies[k][1])[:limite]
    elegidos = list(primeros)
    if len(elegidos) < limite and " " not in q:
        vistos = set(elegidos)
        palabra = " " + q
        otros = [k for k, c in enumerate(indice.claves) if palabra in c and k not in vistos]
        elegidos += sorted(otros, key=lambda k: -indice.especies[k][1])[:limite - len(elegidos)]
    return [{"nombre": e[0], "registros": e[1], "familia": e[2], "clase": e[3], "aphia_id": e[4]}
            for e in (indice.especies[k] for k in elegidos)]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    actualizar()
