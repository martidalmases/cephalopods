# Cephalopods — NASA Space Apps Challenge 2026

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | Harvester and FastAPI service: monthly global ocean data since 1980 on 1° and 0.5° grids (NASA MERRA-2 and SMAP, NOAA OISST, NOAA ERDDAP MODIS), yearly species habitat suitability at 0.5° (OBIS) and the real data of the Trends screen. Deployment files in `backend/despliegue/`. Docs in English and Spanish. |
| [`trend-detective/`](trend-detective/) | Static web app to explore environmental trends as stacked map layers. Live at https://trend.cooking. |
| [`notebooks/`](notebooks/) | Exploratory notebooks and scripts (species info, Giovanni API calls). |
| [`tools/earthdata-auth/`](tools/earthdata-auth/) | Helper scripts that create local NASA Earthdata credentials (`~/.netrc`, `~/.dodsrc`, `~/.edl_token`). |

Each folder has its own README or instructions. The backend runs from inside `backend/`:

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api:app --reload
```
