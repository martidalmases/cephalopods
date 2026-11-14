"""
Pasada única: deja vacías las celdas de tierra de ambiente.zarr, año a año (poca RAM).

El recolector ya no guarda la tierra, pero los datos cargados con versiones anteriores sí la
tienen (un tercio del planeta en MERRA-2). Vaciarla libera disco: los trozos vacíos no ocupan.
Ejecutar sin que el recolector esté escribiendo:

    DATOS_DIR=/opt/cephalopods/datos python compactar.py
"""

import numpy as np
import zarr

import almacen

g = zarr.open_group(almacen.RUTA_AMBIENTE, mode="a", use_consolidated=False)
tierra = ~g["mar"][:]
for v in [k for k in g.array_keys() if k not in ("time", "lat", "lon", "mar")]:
    for a in range(0, almacen.NT, 12):
        b = g[v][a:a + 12]
        if np.isnan(b).all():
            continue
        b[:, tierra] = np.nan
        g[v][a:a + 12] = b
    print(v, "ok", flush=True)
almacen.consolidar()
