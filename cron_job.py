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
ACTION_MODE = os.getenv("ACTION_MODE", "all")

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
            user_id TEXT NOT NULL, username TEXT, profile_pic_url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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

def log_action(conn, action_type, user_id, username, profile_pic_url):
    """Logs an individual action (unfollow/remove) to the database."""
    cursor = conn.cursor()
    cursor.execute("INSERT INTO actions (action_type, user_id, username, profile_pic_url) VALUES (?, ?, ?, ?)",
                   (action_type, user_id, username, profile_pic_url))
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
    """Main logic for syncing followers, now with controllable modes."""
    if not all([ACCOUNT_USERNAME, ACCOUNT_PASSWORD, PROXY_URL, SESSION_FILE]):
        print("CRITICAL ERROR: Missing ACCOUNT_USERNAME, ACCOUNT_PASSWORD, or PROXY_URL environment variable.")
        return

    summary_log = []
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

        if not cl.user_id:
            raise Exception("LOGIN VALIDATION FAILED: instagrapi returned no user_id after login")

        msg = f"Session is valid and ready for user_id {cl.user_id}."
        print(msg)
        run_details_log.append(msg)
        
        user_id = cl.user_id
        if not user_id:
            user_id = cl.user_id_from_username(ACCOUNT_USERNAME)
        
        msg = "Fetching account information..."
        print(msg)
        run_details_log.append(msg)

        account_info = None
        stats_source = None
        try:
            account_info = cl.user_info_v1(user_id).model_dump()
            stats_source = "user_info_v1"
        except Exception as primary_error:
            msg = f"user_info_v1 failed ({primary_error}); retrying with user_info_by_username_v1."
            print(msg)
            run_details_log.append(msg)
            try:
                account_info = cl.user_info_by_username_v1(ACCOUNT_USERNAME).model_dump()
                stats_source = "user_info_by_username_v1"
            except Exception as username_error:
                msg = f"user_info_by_username_v1 failed ({username_error}); retrying with user_info(use_cache=False)."
                print(msg)
                run_details_log.append(msg)
                try:
                    account_info = cl.user_info(user_id, use_cache=False).model_dump()
                    stats_source = "user_info_fallback"
                except Exception as final_error:
                    raise Exception(f"Unable to fetch account info via any method: {final_error}")
        
        msg = f"Account info loaded via {stats_source}."
        print(msg)
        run_details_log.append(msg)
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

        whitelist_ids = set()
        if WHITELIST_USERS and WHITELIST_USERS[0] != '':
            msg = f"Processing whitelist: {WHITELIST_USERS}"
            print(msg)
            run_details_log.append(msg)
            for username in WHITELIST_USERS:
                try:
                    user_id_w = cl.user_id_from_username(username.strip())
                    whitelist_ids.add(user_id_w)
                    msg = f"  > Whitelisted user '{username}' (ID: {user_id_w})"
                    print(msg)
                    run_details_log.append(msg)
                except Exception as e:
                    msg = f"  > Could not find whitelisted user '{username}': {e}"
                    print(msg)
                    run_details_log.append(msg)
        
        msg = "\nFetching followers and following lists..."
        print(msg)
        run_details_log.append(msg)
        followers = cl.user_followers(user_id)
        following = cl.user_following(user_id)
        msg = f"Found {len(followers)} followers and {len(following)} following."
        print(msg)
        run_details_log.append(msg)

        followers_set = set(followers.keys())
        following_set = set(following.keys())
        users_to_unfollow_initial = following_set - followers_set
        users_to_unfollow = users_to_unfollow_initial - whitelist_ids
        whitelisted_spared_count = len(users_to_unfollow_initial) - len(users_to_unfollow)
        users_to_remove = followers_set - following_set

        do_unfollow = ACTION_MODE in ['all', 'unfollow']
        do_remove = ACTION_MODE in ['all', 'remove']

        msg = f"\nAnalysis Complete (Mode: {ACTION_MODE}):"
        print(msg)
        run_details_log.append(msg)
        msg = f"  > {len(users_to_unfollow)} users to unfollow. Will run: {do_unfollow}"
        print(msg)
        run_details_log.append(msg)
        msg = f"  > {whitelisted_spared_count} users spared by whitelist."
        print(msg)
        run_details_log.append(msg)
        msg = f"  > {len(users_to_remove)} followers to remove. Will run: {do_remove}"
        print(msg)
        run_details_log.append(msg)

        conn = sqlite3.connect(DB_PATH)
        
        unfollowed_count = 0
        if do_unfollow:
            for uid in list(users_to_unfollow):
                try:
                    user_short = following.get(uid)
                    username = user_short.username if user_short else f'UserID: {uid}'
                    
                    # ***FIX APPLIED HERE: Convert HttpUrl to string before saving***
                    profile_pic_url_obj = user_short.profile_pic_url if user_short else None
                    profile_pic_url = str(profile_pic_url_obj) if profile_pic_url_obj else None
                    
                    msg = f"Attempting to unfollow: {username} ({uid})"
                    print(msg)
                    run_details_log.append(msg)
                    
                    if cl.user_unfollow(uid):
                        log_action(conn, "unfollow", uid, username, profile_pic_url)
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
        if do_remove:
            for uid in list(users_to_remove):
                try:
                    user_short = followers.get(uid)
                    username = user_short.username if user_short else f'UserID: {uid}'

                    # ***FIX APPLIED HERE: Convert HttpUrl to string before saving***
                    profile_pic_url_obj = user_short.profile_pic_url if user_short else None
                    profile_pic_url = str(profile_pic_url_obj) if profile_pic_url_obj else None
                    
                    msg = f"Attempting to remove follower: {username} ({uid})"
                    print(msg)
                    run_details_log.append(msg)
                    
                    if cl.user_remove_follower(uid):
                        log_action(conn, "remove", uid, username, profile_pic_url)
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
