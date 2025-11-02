import os
import sqlite3
import json
from flask import Flask, render_template

app = Flask(__name__)

# Paths to persistent data on Railway
DB_PATH = "/data/activity.db"
STATS_FILE = "/data/latest_stats.json"

def get_db_connection():
    """Connects to the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Allows accessing columns by name
    return conn

@app.route('/')
def dashboard():
    """The main dashboard page, now with stats and run history."""
    
    # --- NEW: Load latest account stats from the JSON file ---
    stats = {
        "follower_count": "N/A",
        "following_count": "N/A",
        "ratio": "N/A"
    }
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            saved_stats = json.load(f)
            stats["follower_count"] = saved_stats.get("follower_count", "N/A")
            stats["following_count"] = saved_stats.get("following_count", "N/A")
            if isinstance(stats["follower_count"], int) and stats["following_count"] > 0:
                ratio = stats["follower_count"] / stats["following_count"]
                stats["ratio"] = f"{ratio:.2f}"

    if not os.path.exists(DB_PATH):
        return render_template('index.html', db_not_found=True, stats=stats)

    conn = get_db_connection()
    
    # --- Fetch all necessary data ---
    unfollowed = conn.execute("SELECT * FROM actions WHERE action_type = 'unfollow' ORDER BY timestamp DESC LIMIT 50").fetchall()
    removed = conn.execute("SELECT * FROM actions WHERE action_type = 'remove' ORDER BY timestamp DESC LIMIT 50").fetchall()
    unfollow_count = conn.execute("SELECT COUNT(*) FROM actions WHERE action_type = 'unfollow'").fetchone()[0]
    remove_count = conn.execute("SELECT COUNT(*) FROM actions WHERE action_type = 'remove'").fetchone()[0]
    
    # NEW: Fetch the last 20 cron job runs
    run_logs = conn.execute("SELECT id, run_time, status, summary, details FROM run_log ORDER BY run_time DESC LIMIT 20").fetchall()

    conn.close()
    
    return render_template(
        'index.html',
        stats=stats,
        unfollowed_users=unfollowed,
        removed_followers=removed,
        unfollow_count=unfollow_count,
        remove_count=remove_count,
        run_logs=run_logs
    )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)