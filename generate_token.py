"""
generate_token.py - Run this locally to generate/refresh token.json

Usage:
    python generate_token.py

A browser window will open asking you to log in to Google.
Once complete, token.json will be created/updated in this folder.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json
import os

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # Always force a fresh browser login to avoid invalid_grant errors
    if True:
        print("[*] Opening browser for Google login...")
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)

        with open("token.json", "w") as f:
            f.write(creds.to_json())
        print("[*] token.json saved successfully.")
    else:
        print("[*] Token is still valid, no refresh needed.")

    print("\nNEXT STEP: Update the GOOGLE_TOKEN GitHub Secret with the contents of token.json")
    print("    gh secret set GOOGLE_TOKEN --repo familieng9/familycalendarbot")


if __name__ == "__main__":
    main()
