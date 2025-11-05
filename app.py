import os
import sqlite3
import json
import requests
import secrets # Used for generating secure random tokens
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)

# --- CONFIGURATION ---
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID") # ID of the cron service
FRONTEND_URL = os.getenv("FRONTEND_URL")

DB_PATH = "/data/activity.db"
STATS_FILE = "/data/latest_stats.json"

# --- CORS SETUP ---
# Allows your Next.js app to make requests to this API
CORS(app, resources={r"/api/*": {"origins": FRONTEND_URL}})

# --- TOKEN-BASED AUTH ---
# This is a simple, in-memory store for our one valid session token.
# In a larger app, this would be a database.
valid_session_token = None

def get_db_connection():
    """Connects to the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# A helper function to check the Authorization header on protected routes
def is_authorized():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    
    # Extract the token from the "Bearer <token>" header
    token = auth_header.split(' ')[1]
    # Check if the provided token matches our valid one
    return token == valid_session_token

# --- API ENDPOINTS ---

@app.route('/api/login', methods=['POST'])
def login():
    global valid_session_token
    data = request.get_json()
    if not data or 'password' not in data:
        return jsonify({"error": "Password is required"}), 400
    
    if data['password'] == DASHBOARD_PASSWORD:
        # If password is correct, generate a new secure token
        valid_session_token = secrets.token_hex(16)
        # Send the token back to the frontend to be stored
        return jsonify({"message": "Login successful", "token": valid_session_token}), 200
    else:
        return jsonify({"error": "Incorrect password"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    global valid_session_token
    # Invalidate the current token
    valid_session_token = None
    return jsonify({"message": "Logout successful"}), 200

@app.route('/api/dashboard-data')
def dashboard_data():
    if not is_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    # Load stats
    stats = {"follower_count": "N/A", "following_count": "N/A", "ratio": "N/A"}
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            stats = json.load(f)
    
    if not os.path.exists(DB_PATH):
        return jsonify({ "stats": stats, "db_not_found": True })

    conn = get_db_connection()
    unfollowed = conn.execute("SELECT * FROM actions WHERE action_type = 'unfollow' ORDER BY timestamp DESC LIMIT 50").fetchall()
    removed = conn.execute("SELECT * FROM actions WHERE action_type = 'remove' ORDER BY timestamp DESC LIMIT 50").fetchall()
    run_logs = conn.execute("SELECT id, run_time, status, summary FROM run_log ORDER BY run_time DESC LIMIT 20").fetchall()
    conn.close()

    return jsonify({
        "stats": stats,
        "unfollowed_users": [dict(row) for row in unfollowed],
        "removed_followers": [dict(row) for row in removed],
        "run_logs": [dict(row) for row in run_logs]
    })

@app.route('/api/trigger-job', methods=['POST'])
def trigger_job():
    if not is_authorized():
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    mode = data.get('mode')
    if mode not in ['unfollow', 'remove']:
        return jsonify({"error": "Invalid mode specified"}), 400

    if not all([RAILWAY_API_TOKEN, RAILWAY_PROJECT_ID, RAILWAY_SERVICE_ID]):
        return jsonify({"error": "Railway API variables are not configured on the server."}), 500

    url = f"https://backboard.railway.app/api/v2/projects/{RAILWAY_PROJECT_ID}/services/{RAILWAY_SERVICE_ID}/deployments"
    headers = {"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"serviceId": RAILWAY_SERVICE_ID, "variables": {"ACTION_MODE": mode}}

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return jsonify({"message": f"Successfully triggered '{mode}' job!"}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to trigger Railway job: {e}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)