import os
import sqlite3
from flask import Flask, render_template

app = Flask(__name__)

# Must be the same path used by the cron job script
DB_PATH = "/data/activity.db"

def get_db_connection():
    """Connects to the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def dashboard():
    """The main dashboard page."""
    if not os.path.exists(DB_PATH):
        return "<h3>Database not found. Please wait for the first cron job to run.</h3>"

    conn = get_db_connection()
    unfollowed = conn.execute("SELECT * FROM actions WHERE action_type = 'unfollow' ORDER BY timestamp DESC LIMIT 100").fetchall()
    removed = conn.execute("SELECT * FROM actions WHERE action_type = 'remove' ORDER BY timestamp DESC LIMIT 100").fetchall()
    unfollow_count = conn.execute("SELECT COUNT(*) FROM actions WHERE action_type = 'unfollow'").fetchone()[0]
    remove_count = conn.execute("SELECT COUNT(*) FROM actions WHERE action_type = 'remove'").fetchone()[0]
    conn.close()
    
    return render_template('index.html', unfollowed_users=unfollowed, removed_followers=removed, unfollow_count=unfollow_count, remove_count=remove_count)

if __name__ == "__main__":
    # Railway provides the PORT environment variable
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)