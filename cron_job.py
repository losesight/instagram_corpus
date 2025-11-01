import os
import time
import random
import sqlite3
from instagrapi import Client
from instagrapi.exceptions import BadPassword, ChallengeRequired, ClientError
from requests.exceptions import ProxyError

# --- CONFIGURATION (Read from Railway Environment Variables) ---
ACCOUNT_USERNAME = os.getenv("ACCOUNT_USERNAME")
ACCOUNT_PASSWORD = os.getenv("ACCOUNT_PASSWORD")
PROXY_URL = os.getenv("PROXY_URL")

# --- PATHS FOR RAILWAY'S PERSISTENT STORAGE ---
# Railway provides a persistent volume at /data for storing files.
DATA_DIR = "/data"
DB_PATH = os.path.join(DATA_DIR, "activity.db")
SESSION_FILE = os.path.join(DATA_DIR, f"{ACCOUNT_USERNAME}_session.json")

# --- SAFETY SETTINGS ---
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 90

def init_db():
    """Initializes the database and creates the table if it doesn't exist."""
    print(f"Ensuring data directory exists at {DATA_DIR}...")
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    print("Creating 'actions' table if it doesn't exist...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized.")

def log_action(action_type, user_id, username):
    """Logs an action to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO actions (action_type, user_id, username) VALUES (?, ?, ?)",
                   (action_type, user_id, username))
    conn.commit()
    conn.close()
    print(f"Logged action: {action_type} for user {username}")

def run_sync():
    """The main logic for syncing followers and following."""
    if not all([ACCOUNT_USERNAME, ACCOUNT_PASSWORD, PROXY_URL]):
        print("CRITICAL ERROR: Missing environment variables on Railway.")
        return

    print("Initializing client for cron job...")
    cl = Client()
    cl.set_proxy(PROXY_URL)

    try:
        if os.path.exists(SESSION_FILE):
            print(f"Loading session from {SESSION_FILE}...")
            cl.load_settings(SESSION_FILE)
            cl.login(ACCOUNT_USERNAME, ACCOUNT_PASSWORD)
        else:
            print("No session file found. Performing fresh login...")
            cl.login(ACCOUNT_USERNAME, ACCOUNT_PASSWORD)
        
        cl.dump_settings(SESSION_FILE)
        print("Session is valid and saved.")

        user_id = cl.user_id_from_username(ACCOUNT_USERNAME)
        print(f"Fetching data for user_id: {user_id}")
        
        followers = cl.user_followers(user_id)
        following = cl.user_following(user_id)

        followers_set = set(followers.keys())
        following_set = set(following.keys())

        users_to_unfollow = following_set - followers_set
        users_to_remove = followers_set - following_set
        
        print(f"Found {len(users_to_unfollow)} users to unfollow.")
        print(f"Found {len(users_to_remove)} followers to remove.")

        # UNFOLLOW LOGIC
        for uid in list(users_to_unfollow):
            username = following.get(uid, {}).get('username', f'UserID: {uid}')
            print(f"Attempting to unfollow: {username} ({uid})")
            if cl.user_unfollow(uid):
                log_action("unfollow", uid, username)
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

        # REMOVE FOLLOWER LOGIC
        for uid in list(users_to_remove):
            username = followers.get(uid, {}).get('username', f'UserID: {uid}')
            print(f"Attempting to remove follower: {username} ({uid})")
            if cl.user_remove_follower(uid):
                log_action("remove", uid, username)
            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

    except (ProxyError, ChallengeRequired, BadPassword, ClientError) as e:
        print(f"A critical API error occurred: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Cron job finished.")

if __name__ == "__main__":
    init_db()
    run_sync()