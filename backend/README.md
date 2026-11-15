# Ocean state: backend

*[Versión en español más abajo](#estado-del-océano-backend).*

The website does not query NASA on every visit. A **harvester** stores the **monthly mean** of each
variable **for the whole planet** (1° grid), from **January 1980 to today**, in a **Zarr** store, and
the **API** reads it with **xarray**. **Species** results are computed **year by year**.

```
NASA MERRA-2 (OPeNDAP) ─┐                                     ┌── GET /api/datos, /api/mapa, /api/estado
NASA SMAP (OPeNDAP) ────┼─► recolector.py ─► datos/*.zarr ─► api.py ─┼── GET /api/trends/…    ◄── app.js (Trends)
NOAA ERDDAP (MODIS) ────┘   (daily, 04:00)      ▲             └── GET /api/especies/…  ◄── species.js (Species)
OBIS (sightings) ──────────► especies.py ───────┘
```

```
datos/
  ambiente.zarr       one array per variable (time, lat, lon): monthly mean, 1° global grid
                      (180 x 360), one step per month from 1980-01 to 2040-12, in chunks of
                      12 months x 90 x 90 cells (empty chunks take no disk) + sea mask `mar`
  anual.zarr          yearly mean per cell of the habitat variables (anio, lat, lon)
  especies/*.zarr     per species: suitability and limiting factor (anio, lat, lon), sightings
                      per year and cell, and niche; metadata and summary in the attributes
  trends/             JSON files for the Trends screen of the website (exportar_trends.py)
```

| File | What it does |
|---|---|
| `recolector.py` | Fills `ambiente.zarr` with the missing months (and revisits the latest ones). |
| `almacen.py` | Global grid (`DATOS_PASO`, 1° by default), monthly axis, regridding from any source grid, Zarr and xarray. |
| `especies.py` | Yearly means, environmental niche and habitat suitability of each species per year. |
| `exportar_trends.py` | Exports the real data in the format of the Trends screen. |
| `api.py` | FastAPI API for the website. Docs at `/docs`. |
| `datos_mediterraneo.py` | Live daily query for one point (tests and diagnostics) and catalogue of names and units. |
| `compactar.py` | One-off pass that empties land cells loaded by older versions (frees disk). |
| [`../trend-detective/`](../trend-detective/) | The website. The species screen is `species.js` and `species.css`. |
| `despliegue/` | systemd services and timer, and the nginx block. |

## Variables (monthly means)

| Variable | Source | Since |
|---|---|---|
| temperatura | MERRA-2 M2TMNXSLV `TS` (°C) | 1980 |
| dif_temp_mar_aire | M2TMNXSLV `TS − T2M` (°C) | 1980 |
| dif_humedad_mar_aire | qsat(`TS`) − M2TMNXFLX `QLML` (g/kg) | 1980 |
| evaporacion | M2TMNXFLX `EVAP` (mm/day) | 1980 |
| precipitacion | M2TMNXFLX `PRECTOTCORR` (mm/day) | 1980 |
| calor_latente, calor_sensible | M2TMNXFLX `EFLUX`, `HFLUX` (W m⁻²) | 1980 |
| viento_velocidad, viento_direccion | M2TMNXFLX `SPEED`; `ULML` + `VLML` (degrees, where it blows from) | 1980 |
| aerosoles | M2TMNXAER `TOTEXTTAU` (optical depth at 550 nm) | 1980 |
| fitoplancton | NOAA ERDDAP, MODIS Aqua monthly chlorophyll-a (16 pixels of 4 km per cell) | 2003 |
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
- **The sea mask** is the cells with at least 50 % ocean in MERRA-2 (constant file M2C0NXASM). It
  does not depend on NOAA being up. Land cells are not stored.
- **Disk and memory:** about 1 GB of environmental data and a few MB per species. Peak RAM is a few
  hundred MB, because everything is processed by month, by variable and by latitude band.
- **Later runs:** each run requests the months missing anywhere in the period (including gaps left
  by failed loads) and the last 2, which near-real-time sources correct.

## Species (yearly results)

Only the stored environmental variables are used. The only external download is each species'
sightings since 1980, from [OBIS](https://obis.org), which needs no account.

1. **Yearly means**: the mean of each variable per cell and year. A year counts in a cell if it has
   at least 9 months with data; the current year is left out.
2. **Niche**: each sighting is matched to its sea cell (or to the nearest neighbouring sea cell,
   within about one cell, ~111 km at 1°) and to its **year**. The values of that year give the
   p5–p95 percentiles of each variable.
3. **Suitability** of each cell and year, from 0 to 1, with a BIOCLIM-style environmental envelope
   model. It is computed for each variable and the lowest value is kept. The variable that produces
   it is the *limiting factor*.
4. **Summary**: % of favourable sea each year, its trend (percentage points per decade) and
   sightings per year.

**Which variables enter the yearly index:** only those with data in at least 80 % of the years.
With MERRA-2 these are temperature, sea–air temperature difference, evaporation and wind, from 1980
to today. This keeps the series comparable from start to end: if chlorophyll entered, the index
would jump in 2003 just because new data appear. Chlorophyll, fluorescence and salinity are shown in
the niche, marked as outside the index.

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

## Habitat of a species with the four chosen variables (species portal)

Two new modules, plus the shared statistics:

| File | Role |
|---|---|
| `tendencias.py` | Sen's slope, modified Mann-Kendall (Hamed-Rao) and Benjamini-Hochberg FDR. Used by everything below and by `exportar_trends.py`. Test: `python pruebas/prueba_tendencias.py`. |
| `habitat.py` | Habitat store: the four variables, **yearly, on a global 0.25° grid** (`datos/habitat/`, plain numpy files). Coldest month, hottest month and unusually hot months come from the monthly MERRA-2 TS on its own grid (kept in `datos/habitat/ts/`); chlorophyll from MODIS-Aqua 4 km. |
| `especies_habitat.py` | On request: OBIS sightings worldwide → presences per 0.25° cell and year → profile (percentiles of each variable at the presences, matched by year) → habitat suitability of every Mediterranean cell and year → indicators and their trends → one JSON per species. |
| `regiones.py` | The Mediterranean box and mask shared by the maps. |

Read before relying on the numbers:
- **Temperature at 0.25° has no more detail than MERRA-2** (0.5° × 0.625°): each cell takes the value of the
  MERRA-2 sea cell that contains it. Chlorophyll is real 0.25°.
- **Unusually hot months** are months above the 90th percentile of that calendar month over all complete years
  (as in the variable-selection study), so adding a year changes past values slightly; `BASE_CALIDOS` in
  `habitat.py` fixes a reference period instead. In the suitability index they only count against a place
  when there are more than the species has been seen with (`LADO` in `especies_habitat.py`).
- The yearly index (1980 to today) uses the three temperature variables; chlorophyll starts in 2003 and is a
  second layer (`idoneidad_chl`).
- Indicators: suitable area, centre and poleward limit of the suitable habitat (from the map, yearly);
  observed poleward limit per hemisphere and thermal tolerance (from the sightings, in periods of 1, 3 or 5
  years with at least 30 presences each, all cut to the same number of presences). The p-values of all
  indicators of a species are adjusted together; so are those of the cells of the trend map.

```bash
python habitat.py                       # first time: ~560 requests to MERRA-2 and 276 to ERDDAP; ~1.3 GB on disk
python habitat.py --sin-clorofila       # temperature only
python especies_habitat.py              # the main species
python especies_habitat.py "Thalassoma pavo"
```
The harvester runs both at the end of each run. API: `/api/habitat`, `/api/habitat/especies`,
`/api/habitat/especies/{name}`. `exportar_trends.py` adds the sets `habitat-1980` and `habitat-2003`
(Mediterranean, 0.25°) and the three temperature variables to the 1° and 2° sets.

New in the monthly store: `humedad_especifica` and `vapor_agua` (MERRA-2 QLML and TQV). The first harvester
run after this change asks MERRA-2 again for every month since 1980 (about 1 700 requests), because the two
new variables are empty.

## Trends screen (real data)

`exportar_trends.py` converts `ambiente.zarr` to the format of the Trend Detective website
(`../trend-detective/README.md`). It runs at the end of the harvester. The API serves the files at
`/api/trends/<set>/…` and `app.js` reads them from there (only its `DATA_DIR` line was changed).

The website needs every variable in a set to share the same years, with no gaps. So there is
**one set per region and start year**, with the variables that cover the whole period:

| Set | Cells | Variables |
|---|---|---|
| `mediterraneo-1980`, `mundo-1980` | 1° and 2° | MERRA-2: temperature, aerosols, wind, evaporation, precipitation, heat fluxes and sea–air differences |
| `mediterraneo-2003`, `mundo-2003` | 1° and 2° | + chlorophyll and fluorescence (MODIS) |
| `mediterraneo-2015`, `mundo-2015` | 1° and 2° | + salinity (SMAP) |

The world uses 2° cells so that each variable weighs less than 1 MB compressed. On the website, the
picker in the «Region» section changes the set (`?datos=mundo-1980`), and `?datos=ficheros` shows
the team's static files in `trend-detective/data/` (Mediterranean, 2003–2025, `pipeline/fetch_real_data.py`).

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
`species.js` or `species.css`, raise the `?v=` number of their links in `explore.html` so browsers do
not keep the old version.

**Token:** the Earthdata token expires after 60 days. Renew it in `/etc/cephalopods.env`; the
harvester reads it on every run. Another option is to install `earthaccess` and put the
credentials in a `.netrc` of the `cephalopods` user, so it renews itself.

---

# Estado del océano: backend

La web no consulta a la NASA en cada visita. Un **recolector** guarda la **media mensual** de cada
variable **en todo el planeta** (rejilla de 1°), de **enero de 1980 a hoy**, en un almacén **Zarr**, y
la **API** lo lee con **xarray**. Las **especies** se calculan **año a año**.

```
NASA MERRA-2 (OPeNDAP) ─┐                                     ┌── GET /api/datos, /api/mapa, /api/estado
NASA SMAP (OPeNDAP) ────┼─► recolector.py ─► datos/*.zarr ─► api.py ─┼── GET /api/trends/…    ◄── app.js (Trends)
NOAA ERDDAP (MODIS) ────┘   (cada día, 04:00)   ▲             └── GET /api/especies/…  ◄── species.js (Species)
OBIS (avistamientos) ──────► especies.py ───────┘
```

```
datos/
  ambiente.zarr       un array por variable (time, lat, lon): media mensual, rejilla mundial de 1°
                      (180 x 360), un paso por mes de 1980-01 a 2040-12, en trozos de
                      12 meses x 90 x 90 celdas (los vacíos no ocupan disco) + máscara de mar `mar`
  anual.zarr          media anual por celda de las variables de hábitat (anio, lat, lon)
  especies/*.zarr     por especie: idoneidad y factor limitante (anio, lat, lon), avistamientos
                      por año y celda y nicho; metadatos y resumen en los atributos
  trends/             ficheros JSON para la pantalla Trends de la web (exportar_trends.py)
```

| Archivo | Qué hace |
|---|---|
| `recolector.py` | Rellena `ambiente.zarr` con los meses que falten (y repasa los últimos). |
| `almacen.py` | Rejilla mundial (`DATOS_PASO`, 1° por defecto), eje mensual, paso de cualquier rejilla a la nuestra, Zarr y xarray. |
| `especies.py` | Medias anuales, nicho ambiental e idoneidad del hábitat de cada especie por año. |
| `exportar_trends.py` | Exporta los datos reales al formato de la pantalla Trends. |
| `api.py` | API FastAPI para la web. Documentación en `/docs`. |
| `datos_mediterraneo.py` | Consulta en vivo y diaria de un punto (pruebas y diagnóstico) y catálogo de nombres y unidades. |
| `compactar.py` | Pasada única que vacía las celdas de tierra cargadas por versiones anteriores (libera disco). |
| [`../trend-detective/`](../trend-detective/) | La web. La pantalla de especies son `species.js` y `species.css`. |
| `despliegue/` | Servicios y temporizador de systemd, y el bloque de nginx. |

## Variables (medias mensuales)

| Variable | Fuente | Desde |
|---|---|---|
| temperatura | MERRA-2 M2TMNXSLV `TS` (°C) | 1980 |
| dif_temp_mar_aire | M2TMNXSLV `TS − T2M` (°C) | 1980 |
| dif_humedad_mar_aire | qsat(`TS`) − M2TMNXFLX `QLML` (g/kg) | 1980 |
| evaporacion | M2TMNXFLX `EVAP` (mm/día) | 1980 |
| precipitacion | M2TMNXFLX `PRECTOTCORR` (mm/día) | 1980 |
| calor_latente, calor_sensible | M2TMNXFLX `EFLUX`, `HFLUX` (W m⁻²) | 1980 |
| viento_velocidad, viento_direccion | M2TMNXFLX `SPEED`; `ULML` + `VLML` (grados, de dónde sopla) | 1980 |
| aerosoles | M2TMNXAER `TOTEXTTAU` (espesor óptico a 550 nm) | 1980 |
| fitoplancton | NOAA ERDDAP, MODIS Aqua clorofila-a mensual (16 píxeles de 4 km por celda) | 2003 |
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
- **La máscara de mar** son las celdas con al menos un 50 % de océano en MERRA-2 (fichero
  constante M2C0NXASM). No depende de que NOAA responda. Las celdas de tierra no se guardan.
- **Espacio y memoria:** alrededor de 1 GB de datos ambientales y pocos MB por especie. El pico de
  RAM es de unos cientos de MB, porque todo se procesa por meses, por variables y por franjas de
  latitud.
- **Ejecuciones siguientes:** cada ejecución pide los meses que falten en todo el periodo (también
  los huecos de cargas que fallaron) y los 2 últimos, que las fuentes en tiempo casi real corrigen.

## Especies (resultados anuales)

Usa solo las variables ambientales guardadas. De fuera solo se descargan los avistamientos de cada
especie desde 1980, desde [OBIS](https://obis.org), que no pide registro.

1. **Medias anuales**: la media de cada variable por celda y año. Un año cuenta en una celda si
   tiene al menos 9 meses con dato; el año en curso queda fuera.
2. **Nicho**: cada avistamiento se asocia a su celda de mar (o a la celda de mar vecina más
   cercana, a menos de una celda, ~111 km a 1°) y a su **año**. Con los valores de ese año se
   calculan los percentiles p5–p95 de cada variable.
3. **Idoneidad** de cada celda y año, de 0 a 1, con un modelo de envolvente ambiental tipo BIOCLIM.
   Se calcula para cada variable y se queda el valor más bajo. La variable que lo produce es el
   *factor limitante*.
4. **Resumen**: % de mar favorable cada año, su tendencia (puntos porcentuales por década) y
   avistamientos por año.

**Qué variables entran en el índice anual:** solo las que tienen datos en al menos el 80 % de los
años. Con MERRA-2 son temperatura, diferencia de temperatura mar-aire, evaporación y viento, de
1980 a hoy. Así la serie es comparable de principio a fin: si entrara la clorofila, el índice
cambiaría en 2003 solo porque aparece un dato nuevo. Clorofila, fluorescencia y salinidad se ven
en el nicho, marcadas como fuera del índice.

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

## Hábitat de una especie con las cuatro variables elegidas (portal de especies)

Dos módulos nuevos, más la estadística común:

| Fichero | Función |
|---|---|
| `tendencias.py` | Pendiente de Sen, Mann-Kendall modificado (Hamed-Rao) y FDR de Benjamini-Hochberg. Lo usan todos los de abajo y `exportar_trends.py`. Prueba: `python pruebas/prueba_tendencias.py`. |
| `habitat.py` | Almacén de hábitat: las cuatro variables, **anuales, en una rejilla mundial de 0,25°** (`datos/habitat/`, ficheros numpy). El mes más frío, el más cálido y los meses anómalamente cálidos salen del TS mensual de MERRA-2 en su propia rejilla (guardado en `datos/habitat/ts/`); la clorofila, de MODIS-Aqua 4 km. |
| `especies_habitat.py` | A demanda: avistamientos de OBIS de todo el mundo → presencias por celda de 0,25° y año → perfil (percentiles de cada variable en las presencias, año a año) → idoneidad de cada celda del Mediterráneo y año → indicadores y sus tendencias → un JSON por especie. |
| `regiones.py` | Caja y máscara del Mediterráneo, comunes a todos los mapas. |

Antes de fiarse de los números:
- **La temperatura a 0,25° no tiene más detalle que MERRA-2** (0,5° × 0,625°): cada celda toma el valor de la
  celda de mar de MERRA-2 que la contiene. La clorofila sí es de 0,25° reales.
- **Meses anómalamente cálidos**: meses por encima del percentil 90 de ese mes del calendario sobre todos los
  años completos (como en el estudio de selección de variables), así que al añadir un año los valores pasados
  cambian un poco; `BASE_CALIDOS` en `habitat.py` fija un periodo de referencia. En el índice de idoneidad sólo
  cuentan en contra cuando hay más de los que la especie ha soportado (`LADO` en `especies_habitat.py`).
- El índice anual (1980 a hoy) usa las tres variables de temperatura; la clorofila empieza en 2003 y es una
  segunda capa (`idoneidad_chl`).
- Indicadores: superficie idónea, centro y límite hacia el polo del hábitat idóneo (del mapa, anuales); límite
  hacia el polo observado por hemisferio y tolerancia térmica (de los avistamientos, en periodos de 1, 3 o 5
  años con al menos 30 presencias, todos reducidos al mismo número de presencias). Las p de todos los
  indicadores de una especie se ajustan juntas; también las de las celdas del mapa de tendencia.

```bash
python habitat.py                       # primera vez: ~560 peticiones a MERRA-2 y 276 a ERDDAP; ~1,3 GB en disco
python habitat.py --sin-clorofila       # sólo temperatura
python especies_habitat.py              # las especies principales
python especies_habitat.py "Thalassoma pavo"
```
El recolector ejecuta los dos al final de cada pasada. API: `/api/habitat`, `/api/habitat/especies`,
`/api/habitat/especies/{nombre}`. `exportar_trends.py` añade los conjuntos `habitat-1980` y `habitat-2003`
(Mediterráneo, 0,25°) y las tres variables de temperatura a los conjuntos de 1° y 2°.

Nuevo en el almacén mensual: `humedad_especifica` y `vapor_agua` (QLML y TQV de MERRA-2). La primera pasada del
recolector tras este cambio vuelve a pedir a MERRA-2 todos los meses desde 1980 (unas 1 700 peticiones), porque
las dos variables nuevas están vacías.

## Pantalla Trends (datos reales)

`exportar_trends.py` convierte `ambiente.zarr` al formato de la web Trend Detective
(`../trend-detective/README.md`). Se ejecuta al final del recolector. La API sirve los ficheros en
`/api/trends/<conjunto>/…` y `app.js` los lee de ahí (solo se ha cambiado su línea `DATA_DIR`).

La web exige que todas las variables de un conjunto tengan los mismos años, sin huecos. Por eso
hay un **conjunto por región y por año de inicio**, con las variables que cubren todo ese periodo:

| Conjunto | Celdas | Variables |
|---|---|---|
| `mediterraneo-1980`, `mundo-1980` | 1° y 2° | MERRA-2: temperatura, aerosoles, viento, evaporación, precipitación, flujos de calor y diferencias mar-aire |
| `mediterraneo-2003`, `mundo-2003` | 1° y 2° | + clorofila y fluorescencia (MODIS) |
| `mediterraneo-2015`, `mundo-2015` | 1° y 2° | + salinidad (SMAP) |

El mundo va a 2° para que cada variable pese menos de 1 MB comprimida. En la web, el selector de
la sección «Region» cambia de conjunto (`?datos=mundo-1980`), y `?datos=ficheros` muestra los
ficheros estáticos del equipo en `trend-detective/data/` (Mediterráneo, 2003–2025, `pipeline/fetch_real_data.py`).

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
`species.js` o `species.css`, sube el número `?v=` de sus enlaces en `explore.html` para que los
navegadores no usen la versión antigua.

**Token:** el de Earthdata caduca a los 60 días. Renuévalo en `/etc/cephalopods.env`; el recolector
lo lee en cada ejecución. Otra opción es instalar `earthaccess` y poner las credenciales en un
`.netrc` del usuario `cephalopods`; así se renueva solo.
