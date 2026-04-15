import os
import json
import time
import asyncio
import aiohttp
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, send_from_directory, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder='static/landing', static_url_path='')
CORS(app)
BASE_DIR = os.getcwd()

# ── Land points ────────────────────────────────────────────────────────────────
LAND_POINTS_PATH = os.path.join(BASE_DIR, 'europe_land_points.json')
_land_points = None

def get_land_points():
    global _land_points
    if _land_points is None:
        with open(LAND_POINTS_PATH) as f:
            _land_points = json.load(f)
    return _land_points

# ── Cache: cache_key → (timestamp, payload_dict) ─────────────────────────────
_heatmap_cache = {}
CACHE_TTL = 3 * 3600  # 3 hours

POLLEN_KEYS = ['birch_pollen', 'grass_pollen', 'ragweed_pollen', 'alder_pollen', 'mugwort_pollen']

def get_db_connection():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    return conn

# ── Async fetcher ──────────────────────────────────────────────────────────────
async def fetch_one(session, lat, lng, day_str):
    keys = ','.join(POLLEN_KEYS)
    url = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lng}"
        f"&hourly={keys}&forecast_days=6&timezone=UTC"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None
            data = await r.json()
            times = data['hourly']['time']
            result = {'lat': lat, 'lng': lng}
            for pk in POLLEN_KEYS:
                vals = [
                    data['hourly'][pk][i]
                    for i, t in enumerate(times)
                    if t.startswith(day_str) and data['hourly'][pk][i] is not None
                ]
                result[pk] = max(vals) if vals else 0.0
            return result
    except Exception:
        return None

async def fetch_all_points(points, day_str, concurrency=40):
    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency)

    async def guarded(session, lat, lng):
        async with sem:
            return await fetch_one(session, lat, lng, day_str)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [guarded(session, lat, lng) for lat, lng in points]
        results = await asyncio.gather(*tasks)

    return [r for r in results if r is not None]

def run_fetch(points, day_str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_all_points(points, day_str))
    finally:
        loop.close()

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def serve_landing():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/assets/<path:path>')
def serve_assets(path):
    return send_from_directory(os.path.join(app.static_folder, 'assets'), path)

@app.route("/map")
def serve_map():
    return render_template("index.html")

@app.route('/static/<path:filename>')
def serve_icons(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

@app.route("/api/heatmap")
def heatmap():
    """
    GET /api/heatmap?day=2025-04-16&type=birch_pollen
    Returns: { success, points: [[lat,lng,value],...], max: float }
    All 1000 European land points, daily peak value for the given pollen type.
    Results are cached in-memory for 3 hours.
    """
    day_str  = request.args.get('day', '').strip()
    pollen_t = request.args.get('type', '').strip()

    if not day_str or pollen_t not in POLLEN_KEYS:
        return jsonify({'success': False, 'error': 'Missing or invalid day/type param'}), 400

    cache_key = f"{day_str}::{pollen_t}"
    now = time.time()

    if cache_key in _heatmap_cache:
        ts, cached = _heatmap_cache[cache_key]
        if now - ts < CACHE_TTL:
            return jsonify({'success': True, **cached})

    points = get_land_points()
    raw = run_fetch(points, day_str)

    if not raw:
        return jsonify({'success': False, 'error': 'No data returned from API'}), 502

    heat_points = [[r['lat'], r['lng'], r[pollen_t]] for r in raw]
    peak = max((p[2] for p in heat_points), default=0)
    payload = {'points': heat_points, 'max': round(peak, 2)}
    _heatmap_cache[cache_key] = (now, payload)

    return jsonify({'success': True, **payload})

@app.route("/api/reviews", methods=['POST'])
def create_review():
    try:
        data = request.json
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO allergy_reviews (center_lat, center_lng, radius_km, review_text)
            VALUES (%s, %s, %s, %s) RETURNING id, created_at
        """, (data['centerLat'], data['centerLng'], data['radiusKm'], data.get('reviewText', '')))
        result = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True, 'id': result[0], 'createdAt': result[1].isoformat()}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/reviews", methods=['GET'])
def get_reviews():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, center_lat, center_lng, radius_km, pollen_type,
                   severity, symptoms, review_text, created_at
            FROM allergy_reviews ORDER BY created_at DESC LIMIT 100
        """)
        reviews = cur.fetchall()
        cur.close(); conn.close()
        return jsonify({'success': True, 'reviews': [dict(r) for r in reviews]}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Note: app.run() omitted. Vercel handles execution via vercel.json.