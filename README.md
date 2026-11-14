# Estado del Mediterráneo: backend

La web no consulta a la NASA en cada visita. Un **recolector** descarga los datos una vez al día,
los guarda en SQLite y la **API** solo lee de la base de datos.

```
NASA Giovanni ──┐                       ┌── GET /api/datos?lat=&lon=
                ├─► recolector.py ─► mediterraneo.sqlite ─► api.py ─┼── GET /api/mapa?variable=
NOAA ERDDAP ────┘   (cada día, 04:00)                       ├── GET /api/variables
                                                            └── GET /api/estado
```

| Archivo | Qué hace |
|---|---|
| `datos_mediterraneo.py` | Consulta en vivo para un punto (pruebas y diagnóstico). Contiene el catálogo de variables. |
| `recolector.py` | Descarga incremental de toda la rejilla del Mediterráneo y la guarda en la BD. |
| `bd.py` | Esquema SQLite, rejilla de 0,5° y búsqueda del punto más cercano. |
| `api.py` | API FastAPI para la web. Documentación en `/docs`. |
| `despliegue/` | Servicios y temporizador de systemd para la máquina virtual. |

## Variables

| Variable | Fuente | Rejilla | Actualización |
|---|---|---|---|
| temperatura | Giovanni, MERRA-2 `TS` | 1° | ~3 semanas de retraso |
| aerosoles | Giovanni, MERRA-2 AOD 550 nm | 1° | ~3 semanas de retraso |
| no2 | Giovanni, OMI | 1° | **sin datos desde el 15/03/2026** |
| salinidad | ERDDAP, SMAP | 0,5° | diaria |
| fitoplancton | ERDDAP, MODIS clorofila-a | 0,5° | compuesto de 8 días |
| fluorescencia | ERDDAP, MODIS nFLH | 0,5° | compuesto de 8 días |

La API de Giovanni solo devuelve series de un punto por petición. Por eso sus variables usan una rejilla de 1°
(unos 260 puntos de mar). ERDDAP devuelve todo el Mediterráneo en una sola petición por variable y mes.

## Prueba en local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export EARTHDATA_TOKEN="..."
.venv/bin/python recolector.py --desde 2026-10-01      # primera carga corta
.venv/bin/uvicorn api:app --reload                     # http://localhost:8000/docs
```

La primera carga sin `--desde` descarga los últimos 365 días. Las variables de ERDDAP tardan unos minutos.
Las de Giovanni tardan más porque hacen una petición por punto, unas 260 por variable.
Las siguientes ejecuciones solo piden los días nuevos.

## Despliegue en una máquina virtual de Azure (Ubuntu)

```bash
sudo useradd --system --create-home --home-dir /opt/mediterraneo mediterraneo
sudo cp -r . /opt/mediterraneo && sudo chown -R mediterraneo: /opt/mediterraneo
sudo -u mediterraneo python3 -m venv /opt/mediterraneo/.venv
sudo -u mediterraneo /opt/mediterraneo/.venv/bin/pip install -r /opt/mediterraneo/requirements.txt

sudo cp despliegue/mediterraneo.env.ejemplo /etc/mediterraneo.env
sudo chmod 600 /etc/mediterraneo.env && sudo nano /etc/mediterraneo.env   # poner EARTHDATA_TOKEN

sudo cp despliegue/*.service despliegue/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediterraneo-api mediterraneo-recolector.timer
sudo systemctl start mediterraneo-recolector        # primera carga ahora (ver: journalctl -fu mediterraneo-recolector)
```

La API escucha en `127.0.0.1:8000`. Hay que publicarla detrás de nginx con HTTPS, en el mismo servidor que la web.
En el grupo de seguridad de Azure solo hacen falta abiertos los puertos 80 y 443.
En producción, cambia `allow_origins` de `api.py` por el dominio de la web.

**Token:** el de Earthdata caduca a los 60 días. Renuévalo en `/etc/mediterraneo.env`.
Otra opción es instalar `earthaccess` y poner las credenciales en `~mediterraneo/.netrc`; así se renueva solo.
