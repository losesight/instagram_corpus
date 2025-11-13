import os
import sqlite3
import json
import requests
import secrets
from flask import Flask, jsonify, request
from flask_cors import CORS

from db_utils import enqueue_run_request, DatabaseNotConfigured

app = Flask(__name__)

# --- CONFIGURATION ---
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID")
RAILWAY_DEPLOYMENT_ID = os.getenv("RAILWAY_DEPLOYMENT_ID")
RAILWAY_TOKEN_HEADER = os.getenv("RAILWAY_TOKEN_HEADER", "Authorization")
FRONTEND_URL = os.getenv("FRONTEND_URL")

DATA_DIR = os.getenv("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "activity.db")
STATS_FILE = os.path.join(DATA_DIR, "latest_stats.json")
GRAPHQL_ENDPOINT = "https://backboard.railway.com/graphql/v2"
ALLOWED_MODES = {"all", "unfollow", "remove"}

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


def trigger_railway_deploy():
    if not all([RAILWAY_API_TOKEN, RAILWAY_DEPLOYMENT_ID]):
        raise RuntimeError("Railway API credentials are incomplete. Check environment variables.")

    query = """
        mutation Restart($deploymentId: String!) {
            deploymentRestart(id: $deploymentId)
        }
    """
    payload = {
        "query": query,
        "variables": {
            "deploymentId": RAILWAY_DEPLOYMENT_ID
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
    }
    if RAILWAY_TOKEN_HEADER and RAILWAY_TOKEN_HEADER.lower() != "authorization":
        headers[RAILWAY_TOKEN_HEADER] = RAILWAY_API_TOKEN

    response = requests.post(GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"Railway API error: HTTP {response.status_code} - {response.text}")

    body = response.json()
    if 'errors' in body:
        raise RuntimeError(f"Railway API returned errors: {body['errors']}")

    success = body.get("data", {}).get("deploymentRestart")
    if success is not True:
        raise RuntimeError(f"Unexpected Railway response: {body}")
    return success

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


@app.route('/api/trigger-job', methods=['POST', 'OPTIONS'])
def trigger_job():
    if request.method == 'OPTIONS':
        return '', 200

    if not is_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json() or {}
    mode = payload.get("mode", "all")
    if mode not in ALLOWED_MODES:
        return jsonify({"error": f"Invalid mode '{mode}'. Allowed values: {sorted(ALLOWED_MODES)}"}), 400

    try:
        request_id = enqueue_run_request(mode)
    except DatabaseNotConfigured as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": f"Failed to record run request: {exc}"}), 500

    try:
        restart_triggered = trigger_railway_deploy()
    except Exception as exc:
        return jsonify({"error": f"Failed to trigger Railway deploy: {exc}"}), 500

    return jsonify({
        "message": f"Run request queued for mode '{mode}'.",
        "request_id": request_id,
        "deployment_restart": restart_triggered
    })
