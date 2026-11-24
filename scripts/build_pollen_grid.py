#!/usr/bin/env python3
"""
Build a pollen "snapshot" for the Anthoscope heatmap.

Pollen forecasts change slowly (CAMS Europe is re-run once a day), so instead
of every visitor hammering the API we sample a regular lat/lon grid over the
CAMS Europe domain a couple of times per day, reduce it to the daily peak per
pollen type, and store the result. The web app then serves that one snapshot
(CDN-cached) and the browser draws the heatmap from it.

Data: CAMS European Air Quality Forecast (Copernicus), fetched via the
Open-Meteo Air Quality API (multi-location requests).

Usage
-----
    # store in Postgres (same DATABASE_URL the app uses)
    DATABASE_URL=postgres://... python scripts/build_pollen_grid.py

    # or just write a JSON file (handy for local development)
    python scripts/build_pollen_grid.py --out static/data/pollen_grid.json --no-db

Rate limits (Open-Meteo free tier): 600 calls/min, 5 000/h, 10 000/day, and
each location in a multi-location request counts as one call. The default
1° grid is 3 053 points, which this script spreads over ~6 minutes; running it
twice a day uses ~6 100 calls/day.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# CAMS Europe domain (same box the map is restricted to)
WEST, EAST, SOUTH, NORTH = -25.0, 45.0, 30.0, 72.0

POLLEN_TYPES = [
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
]

FORECAST_DAYS = 4          # CAMS Europe forecast horizon
BATCH_SIZE = 100           # locations per HTTP request
MAX_CALLS_PER_MIN = 500    # stay safely below the 600/min free limit
KEEP_SNAPSHOTS = 14        # rows kept in the DB (≈ 1 week at 2 runs/day)
USER_AGENT = "Anthoscope pollen snapshot (+https://anthoscope.com)"


def build_grid(step: float):
    nx = int(round((EAST - WEST) / step)) + 1
    ny = int(round((NORTH - SOUTH) / step)) + 1
    lons = [round(WEST + i * step, 4) for i in range(nx)]
    lats = [round(SOUTH + j * step, 4) for j in range(ny)]
    # row-major, south → north, west → east
    points = [(lat, lon) for lat in lats for lon in lons]
    return nx, ny, points


class BadRequest(Exception):
    """Open-Meteo rejected the request (e.g. a point outside the model domain)."""


def _error_reason(e):
    try:
        body = e.read().decode("utf-8", "replace")
        return json.loads(body).get("reason") or body[:300]
    except Exception:
        return str(e)


def fetch_points(points):
    """fetch_batch, but if Open-Meteo rejects the batch, split it to find the
    offending points and return None for those instead of failing the run."""
    try:
        results = fetch_batch(points)
        if len(results) != len(points):
            raise BadRequest(f"expected {len(points)} results, got {len(results)}")
        return results
    except BadRequest as e:
        if len(points) == 1:
            print(f"  skipping {points[0]}: {e}", flush=True)
            return [None]
        print(f"  batch of {len(points)} rejected ({e}); splitting", flush=True)
        mid = len(points) // 2
        return fetch_points(points[:mid]) + fetch_points(points[mid:])


def fetch_batch(points, retries=5):
    params = {
        "latitude": ",".join(f"{p[0]:.4f}" for p in points),
        "longitude": ",".join(f"{p[1]:.4f}" for p in points),
        "hourly": ",".join(POLLEN_TYPES),
        "forecast_days": FORECAST_DAYS,
        "domains": "cams_europe",
        "cell_selection": "nearest",
        "timezone": "GMT",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params, safe=",")
    delay = 15
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else [data]
        except urllib.error.HTTPError as e:
            reason = _error_reason(e)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                print(f"  HTTP {e.code} ({reason}), retrying in {delay}s", flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            if e.code == 400:
                raise BadRequest(reason) from None
            raise RuntimeError(f"Open-Meteo HTTP {e.code}: {reason}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                print(f"  network error ({e}), retrying in {delay}s", flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            raise


def daily_peaks(location: dict, days: list[str]):
    """Return {type: [peak_day0, peak_day1, ...]} with -1 for 'no data'."""
    hourly = location.get("hourly") or {}
    times = hourly.get("time") or []
    out = {}
    for t in POLLEN_TYPES:
        vals = hourly.get(t) or []
        peaks = {d: None for d in days}
        for ts, v in zip(times, vals):
            if v is None:
                continue
            d = ts[:10]
            if d in peaks and (peaks[d] is None or v > peaks[d]):
                peaks[d] = v
        out[t] = [(-1 if peaks[d] is None else int(round(peaks[d]))) for d in days]
    return out


def build_snapshot(step: float):
    nx, ny, points = build_grid(step)
    n = len(points)
    today = datetime.now(timezone.utc).date()
    days = [
        datetime.fromordinal(today.toordinal() + k).date().isoformat()
        for k in range(FORECAST_DAYS)
    ]
    # values[type][day] -> flat list of ny*nx
    values = {t: [[-1] * n for _ in days] for t in POLLEN_TYPES}

    seconds_per_batch = 60.0 * BATCH_SIZE / MAX_CALLS_PER_MIN
    print(f"Grid {nx}×{ny} = {n} points, step {step}°; "
          f"≈{math.ceil(n / BATCH_SIZE) * seconds_per_batch / 60:.1f} min", flush=True)

    first_days_seen = None
    failed = 0
    for start in range(0, n, BATCH_SIZE):
        t0 = time.monotonic()
        batch = points[start:start + BATCH_SIZE]
        results = fetch_points(batch)
        failed += sum(r is None for r in results)
        first_ok = next((r for r in results if r), None)
        if first_days_seen is None and first_ok:
            tm = (first_ok.get("hourly") or {}).get("time") or []
            first_days_seen = sorted({ts[:10] for ts in tm})
            # Trust the API's calendar (it starts at 00:00 today, GMT)
            if first_days_seen:
                days[:] = first_days_seen[:FORECAST_DAYS]
        for k, loc in enumerate(results):
            if loc is None:
                continue
            peaks = daily_peaks(loc, days)
            idx = start + k
            for t in POLLEN_TYPES:
                for d, v in enumerate(peaks[t]):
                    values[t][d][idx] = v
        done = min(start + BATCH_SIZE, n)
        print(f"  {done}/{n}", flush=True)
        if done < n:
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, seconds_per_batch - elapsed))

    if failed:
        print(f"Warning: {failed}/{n} grid points had no data and were left empty", flush=True)
    if failed > n // 2:
        raise RuntimeError("More than half of the grid failed; not storing this snapshot")

    return {
        "v": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "CAMS European Air Quality Forecast via Open-Meteo",
        "stat": "daily_max",
        "unit": "grains/m3",
        "bbox": [WEST, SOUTH, EAST, NORTH],
        "step": step,
        "nx": nx,
        "ny": ny,
        "days": days,
        "types": POLLEN_TYPES,
        "values": values,
    }


DDL = """
CREATE TABLE IF NOT EXISTS pollen_snapshots (
    id SERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pollen_snapshots_generated
    ON pollen_snapshots (generated_at DESC);
"""


# Query parameters some providers add for Prisma/pgbouncer that libpq rejects
_NON_LIBPQ_PARAMS = {"pgbouncer", "connection_limit", "pool_timeout", "schema",
                     "statement_cache_size", "socket_timeout"}


def clean_dsn(dsn: str) -> str:
    dsn = dsn.strip().strip('"').strip("'").strip()
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    parts = urllib.parse.urlsplit(dsn)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k not in _NON_LIBPQ_PARAMS]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def store_in_db(snapshot: dict, dsn: str):
    import psycopg2  # imported lazily so --no-db works without it

    dsn = clean_dsn(dsn)
    host = urllib.parse.urlsplit(dsn).hostname
    print(f"Saving to database at {host} …", flush=True)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=15)
    except Exception as e:
        raise SystemExit(f"Could not connect to the database ({host}): {e}\n"
                         "Check the DATABASE_URL secret: it should be the same Neon "
                         "connection string the Vercel site uses.") from None
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute(
                "INSERT INTO pollen_snapshots (generated_at, payload) VALUES (%s, %s)",
                (snapshot["generated_at"], json.dumps(snapshot, separators=(",", ":"))),
            )
            cur.execute(
                """DELETE FROM pollen_snapshots WHERE id NOT IN (
                       SELECT id FROM pollen_snapshots
                       ORDER BY generated_at DESC LIMIT %s)""",
                (KEEP_SNAPSHOTS,),
            )
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", type=float, default=1.0,
                    help="grid spacing in degrees (default 1.0)")
    ap.add_argument("--out", help="also write the snapshot JSON to this file")
    ap.add_argument("--no-db", action="store_true", help="do not write to Postgres")
    args = ap.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not args.no_db and not dsn:
        ap.error("DATABASE_URL is not set (use --no-db --out FILE for a file only)")

    snap = build_snapshot(args.step)

    nonzero = sum(1 for t in POLLEN_TYPES for day in snap["values"][t] for v in day if v > 0)
    print(f"Done: {nonzero} non-zero cell/day/type values", flush=True)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, separators=(",", ":"))
        print(f"Wrote {args.out}", flush=True)
    if not args.no_db:
        store_in_db(snap, dsn)
        print("Stored snapshot in pollen_snapshots", flush=True)


if __name__ == "__main__":
    sys.exit(main())