import os
from instagrapi import Client

# --- IMPORTANT ---
# This script is ONLY for running locally to generate a session file.

try:
    # 1. Initialize the client
    cl = Client()
    
    # 2. Get your credentials securely
    username = input("Enter your Instagram username: ")
    password = input("Enter your Instagram password: ")

    # 3. The script will now attempt to log in
    print("Attempting to log in...")
    cl.login(username, password)
    
    # --- THIS IS THE CRITICAL PART ---
    # If Instagram asks for 2FA, the library will stop and prompt you here in the terminal.
    # Enter the 6-digit code from your authenticator app or SMS.
    
    # 4. If login is successful, save the authenticated session
    session_file = f"{username}_session.json"
    cl.dump_settings(session_file)
    
    print("\nSUCCESS!")
    print(f"Session file created: '{session_file}'")
    print("You can now upload this file to your Railway persistent volume.")

except Exception as e:
    print(f"\nAn error occurred: {e}")