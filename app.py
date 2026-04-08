from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from flask_cors import CORS

# 1. Tell Flask the static folder is inside static/landing
app = Flask(__name__, static_folder='static/landing', static_url_path='')
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 2. Use the Neon Connection String (Pooled)
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    # Connect using the single string from Vercel Env Vars
    return psycopg2.connect(DATABASE_URL)

# --- FRONTEND ROUTES ---

@app.route("/")
def serve_landing():
    """Serves the React Landing Page from static/landing"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route("/map")
def serve_map():
    """Serves the original Map page from templates"""
    return render_template("index.html", google_api_key=GOOGLE_API_KEY)

# 3. CRITICAL: Serve React's JS and CSS bundles
@app.route('/assets/<path:path>')
def serve_assets(path):
    return send_from_directory(os.path.join(app.static_folder, 'assets'), path)

# --- API ROUTES (Keep these as they were) ---

@app.route("/api/reviews", methods=['POST'])
def create_review():
    try:
        data = request.json
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO allergy_reviews 
            (center_lat, center_lng, radius_km, pollen_type, severity, symptoms, review_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (
            data['centerLat'], data['centerLng'], data['radiusKm'],
            data['pollenType'], data['severity'], data.get('symptoms', []),
            data.get('reviewText', '')
        ))
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'id': result[0], 'createdAt': result[1].isoformat()}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/reviews", methods=['GET'])
def get_reviews():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM allergy_reviews ORDER BY created_at DESC LIMIT 100")
        reviews = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'reviews': reviews}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500