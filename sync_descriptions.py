"""
Syncs Garmin workout descriptions → Strava activity descriptions.
Matches activities by timestamp (within a 2-minute window).
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
from garminconnect import Garmin

# ── CONFIG ──────────────────────────────────────────────────────────────────
GARMIN_EMAIL    = os.environ["GARMIN_EMAIL"]

STRAVA_CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

MATCH_WINDOW_SECONDS = 120   # activities within 2 min are considered the same
DAYS_BACK            = 2     # how many days back to check
# ─────────────────────────────────────────────────────────────────────────────


def get_strava_access_token():
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": STRAVA_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_strava_activities(token, days_back=DAYS_BACK):
    after = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "per_page": 30},
    )
    resp.raise_for_status()
    return resp.json()


def update_strava_description(token, activity_id, description):
    resp = requests.put(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"description": description},
    )
    resp.raise_for_status()
    return resp.json()


def get_garmin_workouts(client, days_back=DAYS_BACK):
    end   = datetime.now().date()
    start = end - timedelta(days=days_back)
    activities = client.get_activities_by_date(
        start.isoformat(), end.isoformat()
    )
    # Fetch full details for each activity to get notes/description
    detailed = []
    for act in activities:
        try:
            detail = client.get_activity(act["activityId"])
            detailed.append(detail)
        except Exception as e:
            print(f"  [WARN] Could not fetch detail for {act['activityId']}: {e}")
            detailed.append(act)
    return detailed


def parse_garmin_start_time(activity):
    """Return UTC datetime from a Garmin activity dict."""
    raw = activity.get("startTimeGMT") or activity.get("startTimeLocal", "")
    # Garmin returns "YYYY-MM-DD HH:MM:SS"
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def parse_strava_start_time(activity):
    """Return UTC datetime from a Strava activity dict."""
    raw = activity["start_date"]          # "2024-05-10T07:30:00Z"
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def build_garmin_description(g_act):
    """
    Pull the best available description text from a Garmin activity.
    Runna writes its workout notes into 'workoutName' and/or 'description'.
    """
    # DEBUG: print all fields so we can find where Runna puts the notes
    print("  [DEBUG] Garmin activity fields:")
    for key, value in g_act.items():
        if value and str(value).strip():
            print(f"    {key}: {str(value)[:120]}")

    parts = []

    name = g_act.get("workoutName") or ""
    desc = g_act.get("description") or ""
    notes = g_act.get("notes") or ""

    if name and name not in desc:
        parts.append(name)
    if desc:
        parts.append(desc)
    if notes and notes not in desc:
        parts.append(notes)

    return "\n".join(parts).strip()


def main():
    print("── Garmin → Strava description sync ──")

    # 1. Strava auth
    print("Getting Strava access token…")
    strava_token = get_strava_access_token()

    # 2. Fetch Strava activities
    print(f"Fetching Strava activities from the last {DAYS_BACK} days…")
    strava_acts = get_strava_activities(strava_token)
    print(f"  Found {len(strava_acts)} Strava activities")

    # 3. Garmin auth + fetch
    print("Logging into Garmin Connect…")
    GARMIN_TOKENS = os.environ["GARMIN_TOKENS"]
    client = Garmin()
    client.garth.loads(GARMIN_TOKENS)

    print(f"Fetching Garmin activities from the last {DAYS_BACK} days…")
    garmin_acts = get_garmin_workouts(client)
    print(f"  Found {len(garmin_acts)} Garmin activities")

    # 4. Match and update
    updated = 0
    skipped = 0

    for s_act in strava_acts:
        s_time = parse_strava_start_time(s_act)
        s_id   = s_act["id"]
        s_name = s_act.get("name", "")

        # Find the closest Garmin activity within the match window
        best_match = None
        best_delta = timedelta(seconds=MATCH_WINDOW_SECONDS + 1)

        for g_act in garmin_acts:
            try:
                g_time = parse_garmin_start_time(g_act)
            except Exception:
                continue
            delta = abs(s_time - g_time)
            if delta < best_delta:
                best_delta = delta
                best_match = g_act

        if best_match is None:
            print(f"  [NO MATCH] '{s_name}' at {s_time}")
            skipped += 1
            continue

        description = build_garmin_description(best_match)

        if not description:
            print(f"  [SKIP – no description] '{s_name}'")
            skipped += 1
            continue

        existing_desc = s_act.get("description") or ""
        if existing_desc.strip() == description:
            print(f"  [SKIP – already up to date] '{s_name}'")
            skipped += 1
            continue

        print(f"  [UPDATE] '{s_name}' -> '{description[:60]}...'")
        update_strava_description(strava_token, s_id, description)
        updated += 1

    print(f"\nDone. Updated: {updated}  |  Skipped: {skipped}")


if __name__ == "__main__":
    main()