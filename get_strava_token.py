"""
Run this ONCE locally to get your Strava refresh token.
You never need to run it again — the refresh token is long-lived.

Usage:
    pip install requests
    python get_strava_token.py
"""

import requests
import webbrowser

CLIENT_ID     = input("Paste your Strava Client ID: ").strip()
CLIENT_SECRET = input("Paste your Strava Client Secret: ").strip()

auth_url = (
    f"https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri=http://localhost"
    f"&approval_prompt=force"
    f"&scope=activity:read_all,activity:write"
)

print("\nOpening browser for Strava authorization…")
webbrowser.open(auth_url)

print("\nAfter approving, you'll be redirected to a localhost URL that won't load.")
print("Copy the full URL from your browser's address bar and paste it here.")
redirected = input("\nPaste the redirect URL: ").strip()

# Extract the code param
code = redirected.split("code=")[1].split("&")[0]

resp = requests.post("https://www.strava.com/oauth/token", data={
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code":          code,
    "grant_type":    "authorization_code",
})
resp.raise_for_status()
data = resp.json()

print("\n✅ Success! Add these to your GitHub repo secrets:\n")
print(f"  STRAVA_CLIENT_ID     = {CLIENT_ID}")
print(f"  STRAVA_CLIENT_SECRET = {CLIENT_SECRET}")
print(f"  STRAVA_REFRESH_TOKEN = {data['refresh_token']}")
