
# Anthoscope
Anthoscope is an interactive web platform designed to help people manage their pollen allergies more effectively. It combines real-time pollen data, user input, and geospatial visualization to deliver clear, map-based insights into allergy risks across regions. Through a color-coded map, users can instantly identify safe or high-risk areas based on pollen concentration. What makes Anthoscope unique is its community-driven component: users can share their personal experiences and rate areas according to their allergy symptoms. This feedback loop creates a continuously improving, human-centered database that complements scientific forecasts with lived experiences. By combining environmental data and citizen input, Anthoscope promotes public awareness and healthier decisions for individuals with allergies, supporting both preventive healthcare and outdoor well-being. The project prototype already visualizes pollen data on an interactive map, marking the first step toward a comprehensive environmental health platform.
## Contributors
The team behind Anthoscope:
- **Alexandros Mylonas,** University of Bonn
- **Evaggelia Patsatzaki,** Aristotle University of Thessaloniki
- **Nikos Chatzis,** Aristotle University of Thessaloniki
- **Georgios Vellios,** International Hellenic University
- **Maria Maragkou,** Technical University of Madrid
- **Grigoris Kaitzis,** Aristotle University of Thessaloniki


## Installation

To install and run the code, you have to clone the repository to your computer usen the following command line:
```bash
  git clone https://github.com/almylonas/anthoscope
```
Install all the required libraries, using the command:
```bash
  cd anthoscope
  pip install -r requirements.txt
```

## Database setup and run locally

To setup the databse, you need to install [PostgreSQL](https://www.postgresql.org/) locally on your system.
Then, open your terminal and connect to PostgreSQL:
```bash
psql -U postgres
```
Then run the SQL setup script:
```bash
\i setup_database.sql
```
You can exit psql with `\q`
Then, test the database connection (optional):
```bash
python -c "import psycopg2; conn = psycopg2.connect(dbname='pollen_db', user='postgres', password='postgres', host='localhost'); print('Connection successful!'); conn.close()"
```
Finally, run the application:
```bash
DATABASE_URL=postgresql://postgres:postgres@localhost/pollen_db python app.py
```
The app should start on `http://localhost:5000`

## Map & data stack (all open)

| Part | What we use | Notes |
|---|---|---|
| Basemap | [MapLibre GL JS](https://maplibre.org/) + [OpenFreeMap](https://openfreemap.org/) | No API key, no usage cap. `positron` style in light mode, `dark` style in dark mode. |
| Pollen forecast | [CAMS European Air Quality Forecast](https://atmosphere.copernicus.eu/) via the [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) | 0.1° grid, 4-day hourly forecast; alder, birch, grass, mugwort, olive, ragweed. Attribution (CC BY 4.0) is shown on the map. |
| Place search | [Photon](https://photon.komoot.io/) (OSM) | Built for search-as-you-type; results limited to the coverage area. |

Open-Meteo's free tier is for non-commercial use (< 10 000 calls/day). If Anthoscope ever becomes commercial, either take an Open-Meteo subscription or pull CAMS directly from the Copernicus Atmosphere Data Store (free, commercial use allowed).

## Pollen heatmap

Pollen forecasts only change once a day, so the heatmap is served from a saved **snapshot** instead of live API calls:

1. `scripts/build_pollen_grid.py` samples a 1° grid over Europe (3 053 points) from Open-Meteo, keeps the **daily peak** per pollen type for 4 days, and stores it in the `pollen_snapshots` table (last 14 kept).
2. `.github/workflows/pollen-snapshot.yml` runs it at 04:23 and 16:23 UTC (~6 min each, ~6 100 API calls/day).
3. `GET /api/pollen/grid` returns the latest snapshot; it is cached in memory and at Vercel's CDN, so the database is barely touched.
4. The map draws it client-side (bilinear interpolation, projected to Web Mercator) under the place labels.

**Setup once:** in GitHub → *Settings → Secrets and variables → Actions*, add `DATABASE_URL` (the same Neon connection string Vercel uses). Then open *Actions → Pollen heatmap snapshot → Run workflow* to create the first snapshot.

**Local development without a database:**
```bash
python scripts/build_pollen_grid.py --no-db --out static/data/pollen_grid.json
python app.py   # /api/pollen/grid falls back to that file
```
Use `--step 2` for a quicker, coarser grid while testing.
