import os
import time
import random
import sqlite3
import json
import io
import sys
from datetime import datetime
from instagrapi import Client
from json import JSONDecodeError
from instagrapi.exceptions import BadPassword, ChallengeRequired, ClientError
from requests.exceptions import ProxyError

# --- CONFIGURATION (Read from Railway Environment Variables) ---
ACCOUNT_USERNAME = os.getenv("ACCOUNT_USERNAME")
ACCOUNT_PASSWORD = os.getenv("ACCOUNT_PASSWORD")
PROXY_URL = os.getenv("PROXY_URL")
WHITELIST_USERS = os.getenv("WHITELIST", "").split(',')

# --- PATHS FOR RAILWAY'S PERSISTENT STORAGE ---
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "activity.db")
STATS_FILE = os.path.join(DATA_DIR, "latest_stats.json")
SESSION_FILE = os.path.join(DATA_DIR, f"{ACCOUNT_USERNAME}_session.json") if ACCOUNT_USERNAME else None

# --- SAFETY SETTINGS ---
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 90

def init_db():
    """Initializes the database and creates/updates tables."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action_type TEXT NOT NULL,
            user_id TEXT NOT NULL, username TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL, summary TEXT, details TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized/verified.")

def log_action(conn, action_type, user_id, username):
    """Logs an individual action (unfollow/remove) to the database."""
    cursor = conn.cursor()
    cursor.execute("INSERT INTO actions (action_type, user_id, username) VALUES (?, ?, ?)",
                   (action_type, user_id, username))
    conn.commit()

def log_run_history(status, summary, details):
    """Logs the result of the entire cron job run."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO run_log (status, summary, details) VALUES (?, ?, ?)",
                   (status, summary, details))
    conn.commit()
    conn.close()

def run_sync():
    """Main logic for syncing followers, using a persistent session file."""
    if not all([ACCOUNT_USERNAME, ACCOUNT_PASSWORD, PROXY_URL, SESSION_FILE]):
        print("CRITICAL ERROR: Missing ACCOUNT_USERNAME, ACCOUNT_PASSWORD, or PROXY_URL environment variable.")
        return

    summary_log = []
    # ***FIXED LOGGING HERE: Use a simple list instead of redirecting stdout***
    run_details_log = []
    
    try:
        if not os.path.exists(SESSION_FILE):
            msg = "Session file not found. Attempting to create from environment variable..."
            print(msg)
            run_details_log.append(msg)
            session_json_data = os.getenv("INSTAGRAM_SESSION_JSON")
            if session_json_data:
                with open(SESSION_FILE, 'w') as f:
                    f.write(session_json_data)
                msg = "Successfully created session file from INSTAGRAM_SESSION_JSON variable."
                print(msg)
                run_details_log.append(msg)
            else:
                raise Exception("CRITICAL: Session file is missing and INSTAGRAM_SESSION_JSON variable is not set.")

        msg = f"--- Starting Sync Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---"
        print(msg)
        run_details_log.append(msg)
        
        cl = Client()
        cl.set_proxy(PROXY_URL)
        
        msg = f"Loading session from {SESSION_FILE}..."
        print(msg)
        run_details_log.append(msg)
        
        cl.load_settings(SESSION_FILE)
        cl.login(ACCOUNT_USERNAME, ACCOUNT_PASSWORD)
        cl.dump_settings(SESSION_FILE)
        
        msg = "Session is valid and ready."
        print(msg)
        run_details_log.append(msg)
        
        user_id = cl.user_id_from_username(ACCOUNT_USERNAME)
        
        msg = "Fetching account information..."
        print(msg)
        run_details_log.append(msg)
        
        account_info = cl.user_info(user_id).model_dump()
        stats = {
            "username": account_info["username"],
            "follower_count": account_info["follower_count"],
            "following_count": account_info["following_count"],
            "last_updated": datetime.now().isoformat()
        }
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f)
            
        msg = f"Stats saved: {stats['follower_count']} followers, {stats['following_count']} following."
        print(msg)
        run_details_log.append(msg)
        summary_log.append(f"Stats: {stats['follower_count']} followers, {stats['following_count']} following.")

        # ... (The rest of your logic remains the same, but we add messages to the list) ...

        conn = sqlite3.connect(DB_PATH)
        
        unfollowed_count = 0
        for uid in list(users_to_unfollow):
            try:
                user_short = following.get(uid)
                username = user_short.username if user_short else f'UserID: {uid}'
                msg = f"Attempting to unfollow: {username} ({uid})"
                print(msg)
                run_details_log.append(msg)
                if cl.user_unfollow(uid):
                    log_action(conn, "unfollow", uid, username)
                    unfollowed_count += 1
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            except JSONDecodeError as e:
                msg = f"!! WARN: Received a bad response from proxy while unfollowing {uid}. Skipping. Error: {e}"
                print(msg)
                run_details_log.append(msg)
                continue
        if unfollowed_count > 0:
            summary_log.append(f"Unfollowed {unfollowed_count} users.")

        removed_count = 0
        for uid in list(users_to_remove):
            try:
                user_short = followers.get(uid)
                username = user_short.username if user_short else f'UserID: {uid}'
                msg = f"Attempting to remove follower: {username} ({uid})"
                print(msg)
                run_details_log.append(msg)
                if cl.user_remove_follower(uid):
                    log_action(conn, "remove", uid, username)
                    removed_count += 1
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            except JSONDecodeError as e:
                msg = f"!! WARN: Received a bad response from proxy while removing {uid}. Skipping. Error: {e}"
                print(msg)
                run_details_log.append(msg)
                continue
        if removed_count > 0:
            summary_log.append(f"Removed {removed_count} followers.")

        conn.close()
        
        if not summary_log:
            summary_log.append("No actions taken.")

        # ***FIXED LOGGING HERE: Join the list of details instead of using captured output***
        final_summary = " ".join(summary_log)
        final_details = "\n".join(run_details_log)
        print(final_summary)
        log_run_history("SUCCESS", final_summary, final_details)

    except Exception as e:
        error_message = f"ERROR: {type(e).__name__} - {e}"
        print(error_message)
        run_details_log.append(f"\n\n--- SCRIPT FAILED ---\n{error_message}")
        final_details = "\n".join(run_details_log)
        log_run_history("ERROR", error_message, final_details)

    finally:
        print("--- Cron job finished. ---")

if __name__ == "__main__":
    init_db()
    run_sync()