# Ocean state: backend

*[Versión en español más abajo](#estado-del-océano-backend).*

The website does not query NASA on every visit. A **harvester** stores the **monthly mean** of each
variable **for the whole planet**, from **January 1980 to today**, in **Zarr** stores, and the
**API** reads them with **xarray**. Most variables use a **1° grid**; sea temperature (and what is
derived from it) and chlorophyll use a **0.5° grid**. **Species** results are computed **year by
year** on the 0.5° grid.

```
NASA MERRA-2 (OPeNDAP) ─┐                                     ┌── GET /api/datos, /api/mapa, /api/estado
NASA SMAP (OPeNDAP) ────┤                                     │
NOAA OISST (OPeNDAP) ───┼─► recolector.py ─► datos/*.zarr ─► api.py ─┼── GET /api/trends/…    ◄── app.js (Trends)
NOAA ERDDAP (MODIS) ────┘   (daily, 04:00)      ▲             └── GET /api/especies/…  ◄── species.js (Species)
OBIS (sightings) ──────────► especies.py ───────┘
```

```
datos/
  ambiente.zarr       1° grid (180 x 360): one array per variable (time, lat, lon) with the monthly
                      mean, one step per month from 1980-01 to 2040-12, in chunks of
                      12 months x 90 x 90 cells (empty chunks take no disk) + sea mask `mar`
  ambiente_fino.zarr  the same on the 0.5° grid (360 x 720): sea temperature and chlorophyll
  anual.zarr          yearly means and extremes of the habitat variables on the 0.5° grid
  especies/*.zarr     per species: suitability and limiting factor (anio, lat, lon), sightings
                      per year and cell, and niche; metadata and summary in the attributes
  trends/             JSON files for the Trends screen of the website (exportar_trends.py)
```

| File | What it does |
|---|---|
| `recolector.py` | Fills `ambiente.zarr` with the missing months (and revisits the latest ones). |
| `almacen.py` | The two global grids (`BASE` 1°, `FINA` 0.5°), monthly axis, regridding from any source grid, Zarr and xarray. |
| `especies.py` | Yearly means, environmental niche and habitat suitability of each species per year. |
| `exportar_trends.py` | Exports the real data in the format of the Trends screen. |
| `pronostico.py` | 5-year forecast of a yearly series for the Analysis screen (`POST /api/forecast`, Darts). |
| `api.py` | FastAPI API for the website. Docs at `/docs`. |
| `datos_mediterraneo.py` | Live daily query for one point (tests and diagnostics) and catalogue of names and units. |
| `compactar.py` | One-off pass that empties land cells loaded by older versions (frees disk). |
| [`../trend-detective/`](../trend-detective/) | The website. The species screen is `species.js` and `species.css`. |
| `despliegue/` | systemd services and timer, and the nginx block. |

## Variables (monthly means)

| Variable | Source | Since |
|---|---|---|
| **sst** (0.5°) | NOAA OISST v2.1 monthly (0.25°, averaged per 0.5° cell; °C) | 1981-09 |
| **sst_winter_min**, **sst_summer_max** (0.5°, yearly) | coldest and warmest monthly mean of each year (OISST) | 1982 |
| **mhw_months** (0.5°, yearly) | months above the 1982–2011 90th percentile of that month (OISST) | 1982 |
| temperatura | MERRA-2 M2TMNXSLV `TS` (°C, surface skin temperature) | 1980 |
| dif_temp_mar_aire | M2TMNXSLV `TS − T2M` (°C) | 1980 |
| dif_humedad_mar_aire | qsat(`TS`) − M2TMNXFLX `QLML` (g/kg) | 1980 |
| evaporacion | M2TMNXFLX `EVAP` (mm/day) | 1980 |
| precipitacion | M2TMNXFLX `PRECTOTCORR` (mm/day) | 1980 |
| calor_latente, calor_sensible | M2TMNXFLX `EFLUX`, `HFLUX` (W m⁻²) | 1980 |
| viento_velocidad, viento_direccion | M2TMNXFLX `SPEED`; `ULML` + `VLML` (degrees, where it blows from) | 1980 |
| aerosoles | M2TMNXAER `TOTEXTTAU` (optical depth at 550 nm) | 1980 |
| **fitoplancton** (0.5°) | NOAA ERDDAP, MODIS Aqua monthly chlorophyll-a (4 pixels of 4 km per 0.5° cell) | 2003 |
| fluorescencia | NOAA ERDDAP, MODIS Aqua monthly FLH | 2003 |
| salinidad | NASA PO.DAAC, SMAP RSS v6 monthly (0.25°, averaged per cell) | 2015 |

**About the sources:**
- **One request, the whole planet:** each source returns the global map of one month in a single
  request, which is averaged per 1° cell.
  - **MERRA-2 and SMAP:** through NASA OPeNDAP. They need `EARTHDATA_TOKEN`.
  - **MODIS:** through ERDDAP, as binary NetCDF.
- **NO2 (OMI) is no longer collected:** globally it would take about 45,000 Giovanni requests, one
  per cell, and OMI has published no data since March 2026.
- **Before each satellite's start date** there is no data: those months stay empty.
- **The sea masks:** on the 1° grid, cells with at least 50 % ocean in MERRA-2 (constant file
  M2C0NXASM); on the 0.5° grid, cells with OISST data. Land cells are not stored.
- **Marine heatwaves** follow a monthly version of Hobday et al. (2016), which uses daily data: a
  month counts when its mean is above the 90th percentile of that calendar month in 1982–2011.
- **Disk and memory:** about 1.5 GB of environmental data and some 10–25 MB per species. Peak RAM is
  a few hundred MB, because everything is processed by month, by variable and by latitude band.
- **Later runs:** each run requests the months missing anywhere in the period (including gaps left
  by failed loads) and the last 2, which near-real-time sources correct.

## Species (yearly results)

Only the stored environmental variables are used. The only external download is each species'
sightings since 1980, from [OBIS](https://obis.org), which needs no account.

1. **Yearly means**: the mean of each variable per cell and year. A year counts in a cell if it has
   at least 9 months with data; the current year is left out.
2. **Niche**: each sighting is matched to its 0.5° sea cell (or to the nearest neighbouring sea cell,
   within about one cell, ~56 km) and to its **year**. The 1° variables are repeated in the four
   0.5° cells they cover. The values of that year give the
   p5–p95 percentiles of each variable.
3. **Suitability** of each cell and year, from 0 to 1, with a BIOCLIM-style environmental envelope
   model. It is computed for each variable and the lowest value is kept. The variable that produces
   it is the *limiting factor*.
4. **Summary**: % of favourable sea each year, its trend (percentage points per decade) and
   sightings per year.

**Which variables enter the yearly index:** only those with data in at least 80 % of the years:
winter minimum and summer maximum sea temperature, marine heatwave months (OISST), sea–air
temperature difference, evaporation and wind (MERRA-2), from 1982 to today. This keeps the series
comparable from start to end: if chlorophyll entered, the index would jump in 2003 just because new
data appear. Chlorophyll, fluorescence and salinity are shown in the niche, marked as outside the
index.

**When each species is computed:**
- **The main ones** (7 cephalopods in `ESPECIES_PRINCIPALES`) are recomputed at the end of each
  harvester run.
- **The others** are computed the first time the website asks for them at `/api/especies/{nombre}`
  and are then stored. It can take up to a minute, because their sightings worldwide are
  downloaded from OBIS.
- **Automatic recomputation:** if the yearly means change, the species is recomputed on the next
  request.
- **OBIS sightings:** downloaded again every 90 days.

```js
// From the frontend: Python computes the species if needed and returns the result
const datos = await (await fetch("/api/especies/Thunnus thynnus")).json();
const mapa  = await (await fetch("/api/especies/Thunnus thynnus/mapa?anio=2020")).json();
```

**Limitations:**
- **It is a statistical association, not a causal relationship.**
- **Important variables for animals are missing**, such as oxygen, pH or bottom temperature.
- **MERRA-2 is an atmospheric reanalysis** with cells of about 50 km. Its `TS` is the surface
  temperature and, at the coast, mixes land and sea.
- **Sightings are biased** towards the coast, fishing areas and recent years (OBIS has many more
  records since 2020).
- **SMAP salinity is too low in the Mediterranean** (about 35.7 PSU instead of about 37.5).

## Trends screen (real data)

`exportar_trends.py` converts `ambiente.zarr` to the format of the Trend Detective website
(`../trend-detective/README.md`). It runs at the end of the harvester. The API serves the files at
`/api/trends/<set>/…` and `app.js` reads them from there (only its `DATA_DIR` line was changed).

The website needs every variable in a set to share the same years, with no gaps. So there is
**one set per region and start year**, with the variables that cover the whole period:

| Set | Cells | Variables |
|---|---|---|
| `mediterraneo-1980`, `mundo-1980` | 0.5° and 2° | MERRA-2: aerosols, wind, evaporation, precipitation, heat fluxes and sea–air differences |
| `mediterraneo-1982`, `mundo-1982` | 0.5° and 2° | + sea temperature, winter minimum, summer maximum and marine heatwave months (OISST) — the default |
| `mediterraneo-2003`, `mundo-2003` | 0.5° and 2° | + chlorophyll and fluorescence (MODIS) |
| `mediterraneo-2015`, `mundo-2015` | 0.5° and 2° | + salinity (SMAP) |

The world uses 2° cells so that each variable weighs less than 1 MB compressed. On the website, the
picker in the «Region» section changes the set (`?datos=mundo-1980`), and `?datos=ficheros` shows
the team's static files in `trend-detective/data/` (Mediterranean, 2003–2025, `pipeline/fetch_real_data.py`).

## Forecasts

`POST /api/forecast` receives a yearly series (`{"years": [...], "values": [...]}`, 8–100 years, gaps
as `null`) and returns a 5-year forecast with its 90 % interval: damped additive exponential
smoothing from Darts, without seasonality. Gaps of up to two years are interpolated. Darts is
installed as `u8darts`, without PyTorch (the `darts` package pulls it in, several GB). The first
call imports Darts (~3 s); later calls take a few hundredths of a second, with ~220 MB of RAM per API
process.

## Running locally

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.11–3.14 (see note)
export EARTHDATA_TOKEN="..."
.venv/bin/python recolector.py                        # first load: everything since 1980 (computes species at the end)
.venv/bin/uvicorn api:app --reload                     # API and docs: http://localhost:8000/docs
```

**Note on Python 3.15** (recent Fedora): `numcodecs`, a Zarr dependency, has no package for 3.15
yet and must be compiled. Install `sudo dnf install python3-devel` first, or create the environment
with another Python: `uv venv --python 3.13 .venv`. On Debian 13 or Ubuntu 24.04 nothing is needed.

The first load downloads everything since 1980:
- **MERRA-2:** 3 requests per month, about 1,700 in total (about 16 minutes).
- **SMAP:** one per month, about 140 (a couple of minutes).
- **MODIS:** one per month and variable, about 570 (a couple of hours; the NOAA server is slow).

Later runs only request the missing months. With `--desde YYYY-MM-DD` every month from that date is
requested again.

## Deployment (Google Cloud, Debian 13: https://trend.cooking)

In production:
- **Backend:** `/opt/cephalopods`. The code belongs to root and `datos/` to the system user
  `cephalopods`.
- **Website:** Trend Detective in `/var/www/trend-detective`, served by nginx.
- **API:** nginx forwards `https://trend.cooking/api/…` to the backend (`127.0.0.1:8000`), so the
  frontend calls `/api` on the same domain.

```bash
sudo apt-get install -y python3-venv
sudo useradd --system --home-dir /opt/cephalopods --shell /usr/sbin/nologin cephalopods
sudo mkdir -p /opt/cephalopods/datos/cache && sudo cp -r . /opt/cephalopods
sudo chown -R cephalopods: /opt/cephalopods/datos
sudo python3 -m venv /opt/cephalopods/.venv
sudo /opt/cephalopods/.venv/bin/pip install -r /opt/cephalopods/requirements.txt

sudo install -m 600 despliegue/cephalopods.env.ejemplo /etc/cephalopods.env
sudo nano /etc/cephalopods.env        # set EARTHDATA_TOKEN

sudo cp despliegue/cephalopods-*.service despliegue/cephalopods-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cephalopods-api cephalopods-recolector.timer
sudo systemctl start cephalopods-recolector     # first load (follow it: journalctl -fu cephalopods-recolector)
```

**nginx:** `despliegue/trend.cooking.nginx` is the whole site (static web + `/api/`); for an existing site, copy
the block from `despliegue/nginx-api.conf` inside its `server { listen 443 ssl; … }`, before `location /`. Then:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

**Frontend:** copy `../trend-detective/` to `/var/www/trend-detective`. When you change `app.js`,
`species.js` or `species.css`, raise the `?v=` number of their links in `index.html` so browsers do
not keep the old version.

**Token:** the Earthdata token expires after 60 days. Renew it in `/etc/cephalopods.env`; the
harvester reads it on every run. Another option is to install `earthaccess` and put the
credentials in a `.netrc` of the `cephalopods` user, so it renews itself.

---

# Estado del océano: backend

La web no consulta a la NASA en cada visita. Un **recolector** guarda la **media mensual** de cada
variable **en todo el planeta**, de **enero de 1980 a hoy**, en almacenes **Zarr**, y la **API** los
lee con **xarray**. Casi todas las variables van en una **rejilla de 1°**; la temperatura del mar (y
lo que sale de ella) y la clorofila, en una **rejilla de 0,5°**. Las **especies** se calculan **año
a año** en la rejilla de 0,5°.

```
NASA MERRA-2 (OPeNDAP) ─┐                                     ┌── GET /api/datos, /api/mapa, /api/estado
NASA SMAP (OPeNDAP) ────┤                                     │
NOAA OISST (OPeNDAP) ───┼─► recolector.py ─► datos/*.zarr ─► api.py ─┼── GET /api/trends/…    ◄── app.js (Trends)
NOAA ERDDAP (MODIS) ────┘   (cada día, 04:00)   ▲             └── GET /api/especies/…  ◄── species.js (Species)
OBIS (avistamientos) ──────► especies.py ───────┘
```

```
datos/
  ambiente.zarr       rejilla de 1° (180 x 360): un array por variable (time, lat, lon) con la media
                      mensual, un paso por mes de 1980-01 a 2040-12, en trozos de
                      12 meses x 90 x 90 celdas (los vacíos no ocupan disco) + máscara de mar `mar`
  ambiente_fino.zarr  lo mismo en la rejilla de 0,5° (360 x 720): temperatura del mar y clorofila
  anual.zarr          medias y extremos anuales de las variables de hábitat en la rejilla de 0,5°
  especies/*.zarr     por especie: idoneidad y factor limitante (anio, lat, lon), avistamientos
                      por año y celda y nicho; metadatos y resumen en los atributos
  trends/             ficheros JSON para la pantalla Trends de la web (exportar_trends.py)
```

| Archivo | Qué hace |
|---|---|
| `recolector.py` | Rellena `ambiente.zarr` con los meses que falten (y repasa los últimos). |
| `almacen.py` | Las dos rejillas mundiales (`BASE` 1°, `FINA` 0,5°), eje mensual, paso de cualquier rejilla a las nuestras, Zarr y xarray. |
| `especies.py` | Medias anuales, nicho ambiental e idoneidad del hábitat de cada especie por año. |
| `exportar_trends.py` | Exporta los datos reales al formato de la pantalla Trends. |
| `pronostico.py` | Predicción a 5 años de una serie anual para la pantalla Analysis (`POST /api/forecast`, Darts). |
| `api.py` | API FastAPI para la web. Documentación en `/docs`. |
| `datos_mediterraneo.py` | Consulta en vivo y diaria de un punto (pruebas y diagnóstico) y catálogo de nombres y unidades. |
| `compactar.py` | Pasada única que vacía las celdas de tierra cargadas por versiones anteriores (libera disco). |
| [`../trend-detective/`](../trend-detective/) | La web. La pantalla de especies son `species.js` y `species.css`. |
| `despliegue/` | Servicios y temporizador de systemd, y el bloque de nginx. |

## Variables (medias mensuales)

| Variable | Fuente | Desde |
|---|---|---|
| **sst** (0,5°) | NOAA OISST v2.1 mensual (0,25°, media por celda de 0,5°; °C) | 1981-09 |
| **sst_winter_min**, **sst_summer_max** (0,5°, anual) | media mensual más fría y más cálida de cada año (OISST) | 1982 |
| **mhw_months** (0,5°, anual) | meses por encima del percentil 90 de ese mes en 1982–2011 (OISST) | 1982 |
| temperatura | MERRA-2 M2TMNXSLV `TS` (°C, temperatura de la superficie) | 1980 |
| dif_temp_mar_aire | M2TMNXSLV `TS − T2M` (°C) | 1980 |
| dif_humedad_mar_aire | qsat(`TS`) − M2TMNXFLX `QLML` (g/kg) | 1980 |
| evaporacion | M2TMNXFLX `EVAP` (mm/día) | 1980 |
| precipitacion | M2TMNXFLX `PRECTOTCORR` (mm/día) | 1980 |
| calor_latente, calor_sensible | M2TMNXFLX `EFLUX`, `HFLUX` (W m⁻²) | 1980 |
| viento_velocidad, viento_direccion | M2TMNXFLX `SPEED`; `ULML` + `VLML` (grados, de dónde sopla) | 1980 |
| aerosoles | M2TMNXAER `TOTEXTTAU` (espesor óptico a 550 nm) | 1980 |
| **fitoplancton** (0,5°) | NOAA ERDDAP, MODIS Aqua clorofila-a mensual (4 píxeles de 4 km por celda de 0,5°) | 2003 |
| fluorescencia | NOAA ERDDAP, MODIS Aqua FLH mensual | 2003 |
| salinidad | NASA PO.DAAC, SMAP RSS v6 mensual (0,25°, media por celda) | 2015 |

**Sobre las fuentes:**
- **Una petición, el planeta entero:** cada fuente da el mapa mundial de un mes en una sola
  petición, que se promedia por celda de 1°.
  - **MERRA-2 y SMAP:** por OPeNDAP de la NASA. Requieren `EARTHDATA_TOKEN`.
  - **MODIS:** por ERDDAP, en NetCDF binario.
- **El NO2 (OMI) ya no se recoge:** a escala mundial serían unas 45.000 peticiones a Giovanni, una
  por celda, y OMI no publica datos desde marzo de 2026.
- **Antes de las fechas de inicio** de cada satélite no hay datos: esos meses quedan vacíos.
- **Las máscaras de mar:** en la rejilla de 1°, las celdas con al menos un 50 % de océano en MERRA-2
  (fichero constante M2C0NXASM); en la de 0,5°, las celdas con dato de OISST. Las celdas de tierra
  no se guardan.
- **Olas de calor marinas:** versión mensual de Hobday et al. (2016), que usa datos diarios. Un mes
  cuenta si su media supera el percentil 90 de ese mes del año en 1982–2011.
- **Espacio y memoria:** alrededor de 1,5 GB de datos ambientales y unos 10–25 MB por especie. El
  pico de RAM es de unos cientos de MB, porque todo se procesa por meses, por variables y por
  franjas de latitud.
- **Ejecuciones siguientes:** cada ejecución pide los meses que falten en todo el periodo (también
  los huecos de cargas que fallaron) y los 2 últimos, que las fuentes en tiempo casi real corrigen.

## Especies (resultados anuales)

Usa solo las variables ambientales guardadas. De fuera solo se descargan los avistamientos de cada
especie desde 1980, desde [OBIS](https://obis.org), que no pide registro.

1. **Medias anuales**: la media de cada variable por celda y año. Un año cuenta en una celda si
   tiene al menos 9 meses con dato; el año en curso queda fuera.
2. **Nicho**: cada avistamiento se asocia a su celda de mar de 0,5° (o a la celda de mar vecina
   más cercana, a menos de una celda, ~56 km) y a su **año**. Las variables de 1° se repiten en
   las cuatro celdas de 0,5° que cubren. Con los valores de ese año se
   calculan los percentiles p5–p95 de cada variable.
3. **Idoneidad** de cada celda y año, de 0 a 1, con un modelo de envolvente ambiental tipo BIOCLIM.
   Se calcula para cada variable y se queda el valor más bajo. La variable que lo produce es el
   *factor limitante*.
4. **Resumen**: % de mar favorable cada año, su tendencia (puntos porcentuales por década) y
   avistamientos por año.

**Qué variables entran en el índice anual:** solo las que tienen datos en al menos el 80 % de los
años: temperatura mínima de invierno y máxima de verano, meses de ola de calor (OISST), diferencia
de temperatura mar-aire, evaporación y viento (MERRA-2), de 1982 a hoy. Así la serie es comparable
de principio a fin: si entrara la clorofila, el índice cambiaría en 2003 solo porque aparece un
dato nuevo. Clorofila, fluorescencia y salinidad se ven en el nicho, marcadas como fuera del índice.

**Cuándo se calcula cada especie:**
- **Las principales** (7 cefalópodos de `ESPECIES_PRINCIPALES`) se recalculan al final de cada
  ejecución del recolector.
- **Las demás** se calculan la primera vez que la web las pide a `/api/especies/{nombre}` y quedan
  guardadas. Puede tardar hasta un minuto, porque se descargan de OBIS sus avistamientos de todo
  el mundo.
- **Recálculo automático:** si las medias anuales cambian, la especie se recalcula en la
  siguiente petición.
- **Avistamientos de OBIS:** se vuelven a descargar cada 90 días.

```js
// Desde el frontend: Python calcula la especie si hace falta y devuelve el resultado
const datos = await (await fetch("/api/especies/Thunnus thynnus")).json();
const mapa  = await (await fetch("/api/especies/Thunnus thynnus/mapa?anio=2020")).json();
```

**Limitaciones:**
- **Es una asociación estadística, no una relación de causa.**
- **Faltan variables importantes para los animales**, como el oxígeno, el pH o la temperatura
  del fondo.
- **MERRA-2 es un reanálisis atmosférico** con celdas de unos 50 km. Su `TS` es la temperatura de
  la superficie y, en la costa, mezcla tierra y mar.
- **Los avistamientos están sesgados** hacia la costa, las zonas de pesca y los años recientes
  (en OBIS hay muchos más registros desde 2020).
- **La salinidad de SMAP sale baja en el Mediterráneo** (unos 35,7 PSU en lugar de unos 37,5).

## Pantalla Trends (datos reales)

`exportar_trends.py` convierte `ambiente.zarr` al formato de la web Trend Detective
(`../trend-detective/README.md`). Se ejecuta al final del recolector. La API sirve los ficheros en
`/api/trends/<conjunto>/…` y `app.js` los lee de ahí (solo se ha cambiado su línea `DATA_DIR`).

La web exige que todas las variables de un conjunto tengan los mismos años, sin huecos. Por eso
hay un **conjunto por región y por año de inicio**, con las variables que cubren todo ese periodo:

| Conjunto | Celdas | Variables |
|---|---|---|
| `mediterraneo-1980`, `mundo-1980` | 0,5° y 2° | MERRA-2: aerosoles, viento, evaporación, precipitación, flujos de calor y diferencias mar-aire |
| `mediterraneo-1982`, `mundo-1982` | 0,5° y 2° | + temperatura del mar, mínima de invierno, máxima de verano y meses de ola de calor (OISST) — el de por defecto |
| `mediterraneo-2003`, `mundo-2003` | 0,5° y 2° | + clorofila y fluorescencia (MODIS) |
| `mediterraneo-2015`, `mundo-2015` | 0,5° y 2° | + salinidad (SMAP) |

El mundo va a 2° para que cada variable pese menos de 1 MB comprimida. En la web, el selector de
la sección «Region» cambia de conjunto (`?datos=mundo-1980`), y `?datos=ficheros` muestra los
ficheros estáticos del equipo en `trend-detective/data/` (Mediterráneo, 2003–2025, `pipeline/fetch_real_data.py`).

## Predicciones

`POST /api/forecast` recibe una serie anual (`{"years": [...], "values": [...]}`, de 8 a 100 años, con
los huecos como `null`) y devuelve una predicción a 5 años con su intervalo del 90 %: suavizado
exponencial aditivo amortiguado de Darts, sin estacionalidad. Los huecos de hasta dos años se
interpolan. Darts se instala como `u8darts`, sin PyTorch (el paquete `darts` lo arrastra, varios GB).
La primera llamada carga Darts (~3 s); las siguientes tardan unas centésimas de segundo, con ~220 MB
de RAM por proceso de la API.

## Prueba en local

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.11–3.14 (ver nota)
export EARTHDATA_TOKEN="..."
.venv/bin/python recolector.py                        # primera carga: todo desde 1980 (al final calcula las especies)
.venv/bin/uvicorn api:app --reload                     # API y documentación: http://localhost:8000/docs
```

**Nota sobre Python 3.15** (Fedora reciente): `numcodecs`, dependencia de Zarr, aún no tiene paquete
para 3.15 y hay que compilarlo. Instala antes `sudo dnf install python3-devel`, o crea el entorno con
otro Python: `uv venv --python 3.13 .venv`. En Debian 13 o Ubuntu 24.04 no hace falta nada.

La primera carga descarga todo desde 1980:
- **MERRA-2:** 3 peticiones por mes, unas 1.700 en total (unos 16 minutos).
- **SMAP:** una por mes, unas 140 (un par de minutos).
- **MODIS:** una por mes y variable, unas 570 (un par de horas; el servidor de NOAA es lento).

Las ejecuciones siguientes solo piden los meses que falten. Con `--desde AAAA-MM-DD` se vuelven a
pedir todos los meses desde esa fecha.

## Despliegue (Google Cloud, Debian 13: https://trend.cooking)

En producción:
- **Backend:** `/opt/cephalopods`. El código es de root y `datos/` del usuario de sistema `cephalopods`.
- **Web:** Trend Detective en `/var/www/trend-detective`, servida por nginx.
- **API:** nginx pasa `https://trend.cooking/api/…` al backend (`127.0.0.1:8000`), así que el
  frontend llama a `/api` en el mismo dominio.

```bash
sudo apt-get install -y python3-venv
sudo useradd --system --home-dir /opt/cephalopods --shell /usr/sbin/nologin cephalopods
sudo mkdir -p /opt/cephalopods/datos/cache && sudo cp -r . /opt/cephalopods
sudo chown -R cephalopods: /opt/cephalopods/datos
sudo python3 -m venv /opt/cephalopods/.venv
sudo /opt/cephalopods/.venv/bin/pip install -r /opt/cephalopods/requirements.txt

sudo install -m 600 despliegue/cephalopods.env.ejemplo /etc/cephalopods.env
sudo nano /etc/cephalopods.env        # poner EARTHDATA_TOKEN

sudo cp despliegue/cephalopods-*.service despliegue/cephalopods-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cephalopods-api cephalopods-recolector.timer
sudo systemctl start cephalopods-recolector     # primera carga (ver: journalctl -fu cephalopods-recolector)
```

**nginx:** `despliegue/trend.cooking.nginx` es el sitio completo (web estática + `/api/`); para un sitio que ya
existe, copia el bloque de `despliegue/nginx-api.conf` dentro de su `server { listen 443 ssl; … }`, antes de `location /`. Después:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

**Frontend:** copia `../trend-detective/` a `/var/www/trend-detective`. Al cambiar `app.js`,
`species.js` o `species.css`, sube el número `?v=` de sus enlaces en `index.html` para que los
navegadores no usen la versión antigua.

**Token:** el de Earthdata caduca a los 60 días. Renuévalo en `/etc/cephalopods.env`; el recolector
lo lee en cada ejecución. Otra opción es instalar `earthaccess` y poner las credenciales en un
`.netrc` del usuario `cephalopods`; así se renueva solo.
