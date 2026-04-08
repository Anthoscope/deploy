import os
import psycopg2
from flask import Flask, render_template, request, jsonify, send_from_directory
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# 1. Initialize Flask
# We set static_folder to 'static' so the app can "see" your icons 
# (mini, point, etc.) and the 'landing' subfolder simultaneously.
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# 2. Environment Variables (Configured in Vercel Dashboard)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Connect to Neon using the single pooled connection string."""
    # Neon requires SSL, which is usually included in the connection string.
    return psycopg2.connect(DATABASE_URL)

# --- FRONTEND ROUTES ---

@app.route("/")
def serve_landing():
    """Serves the React Landing Page index.html from static/landing/."""
    # This route is hit when a user visits domain.com/.
    return send_from_directory('static/landing', 'index.html')

@app.route('/assets/<path:path>')
def serve_assets(path):
    """
    Serves React JS/CSS bundles.
    This fixes the 404/Blank page by mapping /assets/ to static/landing/assets/.
    """
    return send_from_directory('static/landing/assets', path)

@app.route("/map")
def serve_map():
    """Serves the original Flask Map page from the templates folder."""
    # Uses Jinja2 to inject the API key into templates/index.html.
    return render_template("index.html", google_api_key=GOOGLE_API_KEY)

@app.route('/static/<path:filename>')
def serve_static_files(filename):
    """
    Serves the icons (mini, point, cursor) located directly in the static folder.
    Access these in your map via /static/mini.png etc..
    """
    return send_from_directory('static', filename)

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