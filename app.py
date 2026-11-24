import os
import time
import threading

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, send_from_directory, request, jsonify, Response
from flask_cors import CORS

# Initialize Flask pointing to the landing subfolder
app = Flask(__name__, static_folder='static/landing', static_url_path='')
CORS(app)

# Use absolute paths to prevent Vercel environment confusion
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_GRID_FILE = os.path.join(BASE_DIR, 'static', 'data', 'pollen_grid.json')

# ── Database ────────────────────────────────────────────────────────────────
# One connection per warm serverless instance instead of one per request:
# opening a TLS connection to Neon costs 100–300 ms, reusing it costs nothing.
_conn = None
_conn_lock = threading.Lock()


def _connect():
    return psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5)


def db_run(fn):
    """Run fn(conn) on the shared connection, reconnecting once if it went stale."""
    global _conn
    with _conn_lock:
        for attempt in (0, 1):
            try:
                if _conn is None or _conn.closed:
                    _conn = _connect()
                result = fn(_conn)
                _conn.commit()
                return result
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Neon closes idle connections when the compute suspends
                try:
                    if _conn is not None:
                        _conn.close()
                except Exception:
                    pass
                _conn = None
                if attempt == 1:
                    raise
            except Exception:
                if _conn is not None and not _conn.closed:
                    _conn.rollback()
                raise


# ── Pages & static files ────────────────────────────────────────────────────
@app.route("/")
def serve_landing():
    """Serves index.html from static/landing/"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/assets/<path:path>')
def serve_assets(path):
    """Serves JS/CSS from static/landing/assets/"""
    return send_from_directory(os.path.join(app.static_folder, 'assets'), path)


@app.route("/map")
def serve_map():
    """Serves map app using the template in /templates/"""
    resp = Response(render_template("index.html"))
    resp.headers['Cache-Control'] = 'public, max-age=300, s-maxage=3600, stale-while-revalidate=86400'
    return resp


@app.route('/static/<path:filename>')
def serve_icons(filename):
    """Serves icons sitting directly in /static/ (cursor, mini, point)"""
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename, max_age=86400)


# ── Reviews API ─────────────────────────────────────────────────────────────
@app.route("/api/reviews", methods=['POST'])
def create_review():
    """Create a new allergy review in the Neon database."""
    try:
        data = request.get_json(force=True) or {}
        review_text = (data.get('reviewText') or '').strip()[:2000]

        def insert(conn):
            with conn.cursor() as cur:
                # Only insert the fields the frontend actually sends.
                cur.execute("""
                    INSERT INTO allergy_reviews
                        (center_lat, center_lng, radius_km, review_text)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, created_at
                """, (
                    float(data['centerLat']),
                    float(data['centerLng']),
                    float(data['radiusKm']),
                    review_text,
                ))
                return cur.fetchone()

        new_id, created_at = db_run(insert)
        return jsonify({'success': True, 'id': new_id, 'createdAt': created_at.isoformat()}), 201

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/reviews", methods=['GET'])
def get_reviews():
    """Fetch reviews from the Neon database (briefly CDN-cached)."""
    try:
        def select(conn):
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, center_lat, center_lng, radius_km, review_text, created_at
                    FROM allergy_reviews
                    ORDER BY created_at DESC
                    LIMIT 100
                """)
                return cur.fetchall()

        reviews = db_run(select)
        resp = jsonify({'success': True, 'reviews': [dict(r) for r in reviews]})
        resp.headers['Cache-Control'] = 'public, max-age=0, s-maxage=30, stale-while-revalidate=300'
        return resp, 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Pollen heatmap snapshot ─────────────────────────────────────────────────
# Written by scripts/build_pollen_grid.py (GitHub Action, twice a day).
# Cached in memory per warm instance and at Vercel's CDN, so the database is
# touched at most a few times per hour no matter how many people open the map.
_grid_cache = {'body': None, 'fetched': 0.0}
GRID_MEMORY_TTL = 600  # seconds


def _load_grid_body():
    if os.environ.get('DATABASE_URL'):
        try:
            def latest(conn):
                with conn.cursor() as cur:
                    cur.execute("SELECT payload::text FROM pollen_snapshots "
                                "ORDER BY generated_at DESC LIMIT 1")
                    row = cur.fetchone()
                    return row[0] if row else None
            body = db_run(latest)
            if body:
                return body
        except psycopg2.errors.UndefinedTable:
            pass  # snapshot job has not run yet
        except Exception as e:
            app.logger.warning('pollen grid DB read failed: %s', e)
    if os.path.exists(LOCAL_GRID_FILE):  # local development fallback
        with open(LOCAL_GRID_FILE, encoding='utf-8') as f:
            return f.read()
    return None


@app.route("/api/pollen/grid", methods=['GET'])
def pollen_grid():
    now = time.time()
    if _grid_cache['body'] is None or now - _grid_cache['fetched'] > GRID_MEMORY_TTL:
        body = _load_grid_body()
        if body:
            _grid_cache.update(body=body, fetched=now)
    body = _grid_cache['body']
    if not body:
        resp = jsonify({'success': False, 'error': 'No pollen snapshot available yet'})
        resp.headers['Cache-Control'] = 'public, max-age=60, s-maxage=60'
        return resp, 404
    resp = Response(body, mimetype='application/json')
    resp.headers['Cache-Control'] = 'public, max-age=900, s-maxage=1800, stale-while-revalidate=86400'
    return resp


# Local development: `python app.py`. On Vercel, vercel.json handles execution.
if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
