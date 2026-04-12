"""One-time Gmail OAuth setup script.
Run this once to generate token.json — browser will open for authorization.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path
import json

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CREDS_PATH = "credentials.json"
TOKEN_PATH = "token.json"

print("Opening browser for Gmail authorization...")
flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
creds = flow.run_local_server(port=0)

Path(TOKEN_PATH).write_text(creds.to_json())
print(f"\n✓ token.json saved successfully!")
print(f"  Path: {Path(TOKEN_PATH).absolute()}")
print("\nYou won't need to do this again unless you delete token.json.")
