from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from flask_cors import CORS # Added to help React talk to Flask during dev/prod

app = Flask(__name__, static_folder='static')
CORS(app) # Enables Cross-Origin Resource Sharing

# Load Google API key
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "key")

# Database configuration (Unchanged)
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# --- FRONTEND ROUTES ---

@app.route("/")
def serve_landing():
    """Serves the React Landing Page from static/landing"""
    return send_from_directory('static/landing', 'index.html')

@app.route("/map")
def serve_map():
    """Serves the original Map page from templates"""
    return render_template("index.html", google_api_key=GOOGLE_API_KEY)

# CRITICAL: This route ensures React's CSS/JS assets are found correctly
@app.route('/assets/<path:path>')
def serve_assets(path):
    return send_from_directory('static/landing/assets', path)


# --- API ROUTES (Unchanged) ---

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
        cur.execute("""
            SELECT id, center_lat, center_lng, radius_km, pollen_type, 
                   severity, symptoms, review_text, created_at
            FROM allergy_reviews ORDER BY created_at DESC LIMIT 100
        """)
        reviews = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'reviews': reviews}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

#if __name__ == "__main__":
#    app.run(debug=True)