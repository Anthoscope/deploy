import os
from flask import Flask, send_from_directory

# Tell Flask the default static root is the landing folder
app = Flask(__name__, static_folder='static/landing', static_url_path='')

@app.route("/")
def serve_landing():
    """Serves index.html from static/landing/"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/assets/<path:path>')
def serve_assets(path):
    """
    Explicitly serves files from static/landing/assets/.
    This is what fixes the 404 for index.js and index.css.
    """
    return send_from_directory(os.path.join(app.static_folder, 'assets'), path)

@app.route('/images/<path:path>')
def serve_images(path):
    """Explicitly serves files from static/landing/images/."""
    return send_from_directory(os.path.join(app.static_folder, 'images'), path)

@app.route("/map")
def serve_map():
    """Serves map app using the template in /templates/"""
    return render_template("index.html", google_api_key=os.getenv("GOOGLE_API_KEY"))

@app.route('/static/<path:filename>')
def serve_icons(filename):
    """Serves icons sitting directly in /static/ (cursor, mini, point)"""
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