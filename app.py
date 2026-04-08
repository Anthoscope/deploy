import os
import psycopg2
from flask import Flask, render_template, request, jsonify, send_from_directory
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

# Initialize Flask with the React build folder as the static source
# This ensures that the root directory for static files is your React 'dist' contents
app = Flask(__name__, static_folder='static/landing', static_url_path='')
CORS(app)

# Load Google API key from Vercel Environment Variables
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Get the Neon connection string from Vercel Environment Variables
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Create a database connection to Neon using the pooled connection string"""
    # psycopg2 can take the entire URL directly
    return psycopg2.connect(DATABASE_URL)

# --- FRONTEND ROUTES ---

@app.route("/")
def serve_landing():
    """Serves the React Landing Page index.html"""
    # This sends the index.html from static/landing
    return send_from_directory(app.static_folder, 'index.html')

@app.route("/map")
def serve_map():
    """Serves the original Flask Map page from the templates folder"""
    # This renders templates/index.html and passes the Google API Key
    return render_template("index.html", google_api_key=GOOGLE_API_KEY)

@app.route('/assets/<path:path>')
def serve_assets(path):
    """
    Crucial fix for 404 errors.
    Maps browser requests for /assets/ to the physical static/landing/assets folder.
    """
    return send_from_directory(os.path.join(app.static_folder, 'assets'), path)

# --- API ROUTES ---

@app.route("/api/reviews", methods=['POST'])
def create_review():
    """Create a new allergy review in the Neon database"""
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
    """Fetch reviews from the Neon database"""
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

# Note: if __name__ == "__main__": app.run() is removed for Vercel deployment