import os
import time
import random
import sqlite3
import json
import io
import sys
from datetime import datetime
from instagrapi import Client
from instagrapi.exceptions import BadPassword, ChallengeRequired, ClientError
from requests.exceptions import ProxyError

# --- CONFIGURATION (Read from Railway Environment Variables) ---
ACCOUNT_USERNAME = os.getenv("ACCOUNT_USERNAME")
ACCOUNT_PASSWORD = os.getenv("ACCOUNT_PASSWORD")
PROXY_URL = os.getenv("PROXY_URL")
# NEW: Read the whitelist from an environment variable (comma-separated list)
WHITELIST_USERS = os.getenv("WHITELIST", "").split(',')

# --- PATHS FOR RAILWAY'S PERSISTENT STORAGE ---
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "activity.db")
STATS_FILE = os.path.join(DATA_DIR, "latest_stats.json")
SESSION_FILE = os.path.join(DATA_DIR, f"{ACCOUNT_USERNAME}_session.json")

# --- SAFETY SETTINGS ---
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 90

def init_db():
    """Initializes the database and creates/updates tables."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Main actions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action_type TEXT NOT NULL,
            user_id TEXT NOT NULL, username TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # NEW: Table for logging each cron job run
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
    """Main logic for syncing followers, now with enhanced logging and features."""
    if not all([ACCOUNT_USERNAME, ACCOUNT_PASSWORD, PROXY_URL]):
        print("CRITICAL ERROR: Missing one or more environment variables.")
        return

    summary_log = []
    
    # --- Capture all print() statements for detailed logging ---
    old_stdout = sys.stdout
    sys.stdout = captured_output = io.StringIO()

    try:
        print(f"--- Starting Sync Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        cl = Client()
        cl.set_proxy(PROXY_URL)

        if os.path.exists(SESSION_FILE):
            print(f"Loading session from {SESSION_FILE}...")
            cl.load_settings(SESSION_FILE)
            cl.login(ACCOUNT_USERNAME, ACCOUNT_PASSWORD)
        else:
            print("No session file. Performing fresh login...")
            cl.login(ACCOUNT_USERNAME, ACCOUNT_PASSWORD)
        
        cl.dump_settings(SESSION_FILE)
        print("Session is valid and saved.")
        
        user_id = cl.user_id_from_username(ACCOUNT_USERNAME)
        
        # --- NEW: Get and save latest account stats ---
        print("Fetching account information...")
        account_info = cl.user_info(user_id).dict()
        stats = {
            "username": account_info["username"],
            "follower_count": account_info["follower_count"],
            "following_count": account_info["following_count"],
            "last_updated": datetime.now().isoformat()
        }
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f)
        print(f"Stats saved: {stats['follower_count']} followers, {stats['following_count']} following.")
        summary_log.append(f"Stats: {stats['follower_count']} followers, {stats['following_count']} following.")

        # --- NEW: Whitelist Logic ---
        whitelist_ids = set()
        if WHITELIST_USERS and WHITELIST_USERS[0] != '':
            print(f"Processing whitelist: {WHITELIST_USERS}")
            for username in WHITELIST_USERS:
                try:
                    user_id_w = cl.user_id_from_username(username.strip())
                    whitelist_ids.add(user_id_w)
                    print(f"  > Whitelisted user '{username}' (ID: {user_id_w})")
                except Exception as e:
                    print(f"  > Could not find whitelisted user '{username}': {e}")
        
        print("\nFetching followers and following lists...")
        followers = cl.user_followers(user_id)
        following = cl.user_following(user_id)
        print(f"Found {len(followers)} followers and {len(following)} following.")

        followers_set = set(followers.keys())
        following_set = set(following.keys())

        users_to_unfollow_initial = following_set - followers_set
        
        # --- NEW: Exclude whitelisted users from the unfollow list ---
        users_to_unfollow = users_to_unfollow_initial - whitelist_ids
        whitelisted_spared_count = len(users_to_unfollow_initial) - len(users_to_unfollow)

        users_to_remove = followers_set - following_set

        print(f"\nAnalysis Complete:")
        print(f"  > {len(users_to_unfollow)} users to unfollow.")
        print(f"  > {whitelisted_spared_count} users spared by whitelist.")
        print(f"  > {len(users_to_remove)} followers to remove.")

        conn = sqlite3.connect(DB_PATH) # Open one connection for all actions
        
        # UNFOLLOW LOGIC
        unfollowed_count = 0
        for uid in list(users_to_unfollow):
            username = following.get(uid, {}).get('username', f'UserID: {uid}')
            print(f"Attempting to unfollow: {username} ({uid})")
            if cl.user_unfollow(uid):
                log_action(conn, "unfollow", uid, username)
                unfollowed_count += 1
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
        if unfollowed_count > 0:
            summary_log.append(f"Unfollowed {unfollowed_count} users.")

        # REMOVE FOLLOWER LOGIC
        removed_count = 0
        for uid in list(users_to_remove):
            username = followers.get(uid, {}).get('username', f'UserID: {uid}')
            print(f"Attempting to remove follower: {username} ({uid})")
            if cl.user_remove_follower(uid):
                log_action(conn, "remove", uid, username)
                removed_count += 1
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
        if removed_count > 0:
            summary_log.append(f"Removed {removed_count} followers.")

        conn.close()
        
        if not summary_log:
            summary_log.append("No actions taken.")

        # --- Final logging for success ---
        sys.stdout = old_stdout # Restore standard output
        final_summary = " ".join(summary_log)
        final_details = captured_output.getvalue()
        print(final_summary)
        log_run_history("SUCCESS", final_summary, final_details)

    except Exception as e:
        # --- Final logging for failure ---
        sys.stdout = old_stdout # Restore standard output
        error_message = f"ERROR: {type(e).__name__} - {e}"
        print(error_message)
        # Also capture any logs that happened before the crash
        final_details = captured_output.getvalue() + f"\n\n--- SCRIPT FAILED ---\n{error_message}"
        log_run_history("ERROR", error_message, final_details)

    finally:
        print("--- Cron job finished. ---")


if __name__ == "__main__":
    init_db()
    run_sync()