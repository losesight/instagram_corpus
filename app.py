import os
import sqlite3
import json
import requests
import secrets
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)

# --- CONFIGURATION ---
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID")
FRONTEND_URL = os.getenv("FRONTEND_URL")

DB_PATH = "/data/activity.db"
STATS_FILE = "/data/latest_stats.json"

CORS(app, resources={r"/api/*": {"origins": FRONTEND_URL}})

valid_session_token = None

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_authorized():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    token = auth_header.split(' ')[1]
    return token == valid_session_token

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS': return '', 200
    global valid_session_token
    data = request.get_json()
    if data.get('password') == DASHBOARD_PASSWORD:
        valid_session_token = secrets.token_hex(16)
        return jsonify({"message": "Login successful", "token": valid_session_token}), 200
    return jsonify({"error": "Incorrect password"}), 401

@app.route('/api/dashboard-data')
def dashboard_data():
    if not is_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    
    stats = {}
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f: stats = json.load(f)
    if not os.path.exists(DB_PATH):
        return jsonify({ "stats": stats, "db_not_found": True })

    conn = get_db_connection()
    unfollowed = conn.execute("SELECT * FROM actions WHERE action_type = 'unfollow' ORDER BY timestamp DESC LIMIT 50").fetchall()
    removed = conn.execute("SELECT * FROM actions WHERE action_type = 'remove' ORDER BY timestamp DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify({
        "stats": stats,
        "unfollowed_users": [dict(row) for row in unfollowed],
        "removed_followers": [dict(row) for row in removed]
    })