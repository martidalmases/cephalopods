# Cephalopods — NASA Space Apps Challenge 2026

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | Data collector and FastAPI service for the state of the Mediterranean (NASA Giovanni, NOAA ERDDAP, OBIS) and species habitat suitability. Deployment files in `backend/despliegue/`. Docs in Spanish. |
| [`trend-detective/`](trend-detective/) | Static web app to explore environmental trends as stacked map layers. Live at https://trend.cooking. |
| [`notebooks/`](notebooks/) | Exploratory notebooks and scripts (species info, Giovanni API calls). |
| [`tools/earthdata-auth/`](tools/earthdata-auth/) | Helper scripts that create local NASA Earthdata credentials (`~/.netrc`, `~/.dodsrc`, `~/.edl_token`). |

Each folder has its own README or instructions. The backend runs from inside `backend/`:

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api:app --reload
```
