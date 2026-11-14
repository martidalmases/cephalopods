# Estado del Mediterráneo: backend

La web no consulta a la NASA en cada visita. Un **recolector** descarga los datos una vez al día,
los guarda en un almacén **Zarr** y la **API** lo lee con **xarray**.

```
NASA Giovanni ──┐                                    ┌── GET /api/datos, /api/mapa, /api/estado
                ├─► recolector.py ─► datos/*.zarr ─► api.py ─┤
NOAA ERDDAP ────┘   (cada día, 04:00)   ▲            └── GET /api/especies/…  ◄── fetch() de web/
OBIS (avistamientos) ───► especies.py ──┘
```

```
datos/
  ambiente.zarr       un array por variable (time, lat, lon), rejilla de 0,5°, días 2000–2040
                      (los trozos sin datos no ocupan disco) + máscara de mar `mar` (lat, lon)
  climatologia.zarr   media por mes del año (mes, lat, lon)
  especies/*.zarr     por especie: idoneidad y factor limitante (mes, lat, lon),
                      avistamientos por celda y nicho; metadatos y resumen en los atributos
```

| Archivo | Qué hace |
|---|---|
| `datos_mediterraneo.py` | Consulta en vivo para un punto (pruebas y diagnóstico). Contiene el catálogo de variables. |
| `recolector.py` | Descarga incremental de toda la rejilla del Mediterráneo y la guarda en `ambiente.zarr`. |
| `almacen.py` | Rejilla de 0,5°, escritura en Zarr, lectura con xarray y búsqueda de la celda de mar más cercana. |
| `api.py` | API FastAPI para la web. Documentación en `/docs`. |
| `especies.py` | Nicho ambiental e idoneidad del hábitat de cada especie. |
| `web/` | Página de ejemplo que llama a la API con `fetch()`; se sirve en `/`. |
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
| evaporacion | Giovanni, MERRA-2 M2T1NXFLX `EVAP` (mm/día) | 1° | ~3 semanas de retraso |
| calor_latente | Giovanni, M2T1NXFLX `EFLUX` (W m⁻²) | 1° | ~3 semanas de retraso |
| calor_sensible | Giovanni, M2T1NXFLX `HFLUX` (W m⁻²) | 1° | ~3 semanas de retraso |
| viento_velocidad | Giovanni, M2T1NXFLX `SPEED` (m/s) | 1° | ~3 semanas de retraso |
| viento_direccion | Giovanni, M2T1NXFLX `ULML` + `VLML` (grados, de dónde sopla) | 1° | ~3 semanas de retraso |
| dif_temp_mar_aire | Giovanni, M2T1NXSLV `TS − T2M` (°C) | 1° | ~3 semanas de retraso |
| dif_humedad_mar_aire | Giovanni, qsat(`TS`) − M2I1NXLFO `QLML` (g/kg) | 1° | ~3 semanas de retraso |

La API de Giovanni no ofrece el mensual **M2TMNXFLX**; se usa el horario M2T1NXFLX promediado por día.
Las dos diferencias mar-aire se calculan a partir de varios datasets. La humedad de saturación usa la
fórmula de Bolton con el factor 0,98 por la salinidad y presión estándar (error menor del 2 %).

La API de Giovanni solo devuelve series de un punto por petición. Por eso sus variables usan una rejilla de 1°
(unas 260 celdas de mar). ERDDAP devuelve todo el Mediterráneo en una sola petición por variable y mes.

## Especies

Usa solo las variables ambientales ya guardadas. De fuera solo se descargan los avistamientos de cada
especie, desde [OBIS](https://obis.org), que no pide registro y cuenta los registros desde el año 2000.

1. **Climatología**: la media de cada variable por celda y mes del año (`groupby("time.month")` de xarray).
2. **Nicho**: cada avistamiento se asocia a la celda de mar más cercana (a menos de 50 km) y a su mes.
   Con los valores ambientales de esas celdas se calculan los percentiles p5–p95 de cada variable.
3. **Idoneidad** de cada celda y mes, de 0 a 1, con un modelo de envolvente ambiental tipo BIOCLIM.
   Se calcula para cada variable y se queda el valor más bajo. La variable que lo produce es el
   *factor limitante*.

**Cuándo se calcula cada especie:**
- **Las principales** (7 cefalópodos de `ESPECIES_PRINCIPALES`) se recalculan al final de cada
  ejecución del recolector y siempre están ya guardadas en `datos/especies/`.
- **Las demás** se calculan la primera vez que la web las pide a `/api/especies/{nombre}`, lo que
  tarda unos 10–20 s, y quedan guardadas.
- **Recálculo automático:** si la climatología cambia después, la especie se recalcula en la
  siguiente petición.
- **Avistamientos de OBIS:** se vuelven a descargar cada 90 días.

```js
// Desde el frontend: Python calcula la especie si hace falta y devuelve el resultado
const datos = await (await fetch("/api/especies/Thunnus thynnus")).json();
const mapa  = await (await fetch("/api/especies/Thunnus thynnus/mapa?mes=7")).json();
```

**Limitaciones:**
- **Es una asociación estadística, no una relación de causa.**
- **Faltan variables importantes para los animales**, como el oxígeno, el pH o la temperatura del
  fondo, porque todavía no se descargan.
- **Los avistamientos están sesgados** hacia la costa y las zonas de pesca.
- **La salinidad de SMAP es peor cerca de la costa**, así que tiende a salir como factor limitante.

## Prueba en local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.11–3.14 (ver nota)
export EARTHDATA_TOKEN="..."
.venv/bin/python recolector.py --desde 2026-10-01      # primera carga corta (al final calcula las especies principales)
.venv/bin/uvicorn api:app --reload                     # web: http://localhost:8000  ·  API: /docs
```

**Nota sobre Python 3.15** (Fedora reciente): `numcodecs`, dependencia de Zarr, aún no tiene paquete
para 3.15 y hay que compilarlo. Instala antes `sudo dnf install python3-devel`, o crea el entorno con
otro Python: `uv venv --python 3.13 .venv`. En Ubuntu 24.04 (Azure), con Python 3.12, no hace falta nada.

La primera carga sin `--desde` descarga los últimos 365 días. Las variables de ERDDAP tardan unos minutos.
Las de Giovanni tardan más porque hacen una petición por celda, unas 260 por variable.
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
