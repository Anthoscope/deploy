import os
import psycopg2
from flask import Flask, render_template, request, jsonify, send_from_directory
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# Initialize Flask without a default static folder
app = Flask(__name__, static_folder=None)
CORS(app)

# Environment Variables
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DATABASE_URL = os.getenv('DATABASE_URL')

# Get the absolute path to the current directory
BASE_DIR = os.getcwd()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# --- FRONTEND ROUTES ---

@app.route("/")
def serve_landing():
    # This must point to the folder containing your React index.html
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/assets/<path:path>')
def serve_assets(path):
    """Maps /assets/ requests to the physical static/landing/assets/ folder."""
    return send_from_directory('static/landing/assets', path)

@app.route("/map")
def serve_map():
    return render_template("index.html", google_api_key=GOOGLE_API_KEY)

@app.route('/static/<path:filename>')
def serve_icons(filename):
    """Handles icons (mini, point, etc.) sitting in the static folder"""
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

# --- API ROUTES ---

@app.route("/api/reviews", methods=['POST'])
def create_review():
    """Create a new allergy review in the Neon database."""
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
        
        return jsonify({
            'success': True,
            'id': result[0],
            'createdAt': result[1].isoformat()
        }), 201
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/reviews", methods=['GET'])
def get_reviews():
    """Fetch reviews from the Neon database."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, center_lat, center_lng, radius_km, pollen_type, 
                   severity, symptoms, review_text, created_at
            FROM allergy_reviews
            ORDER BY created_at DESC
            LIMIT 100
        """)
        
        reviews = cur.fetchall()
        cur.close()
        conn.close()
        
        return jsonify({'success': True, 'reviews': reviews}), 200
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Note: app.run() is omitted. Vercel handles execution via vercel.json.