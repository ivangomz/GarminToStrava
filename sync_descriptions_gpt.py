import os
import re
import requests
from datetime import datetime, timedelta, timezone
from garminconnect import Garmin

STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]
GARMIN_TOKENS = os.environ["GARMIN_TOKENS"]

DAYS_BACK = 3
MATCH_WINDOW_SECONDS = 7200  # optional fallback if scheduled time exists


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_strava_access_token():
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "refresh_token": STRAVA_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_strava_activities(token, days_back=DAYS_BACK):
    after = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "per_page": 50},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_strava_description(token, activity_id, description):
    resp = requests.put(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        data={"description": description},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_garmin_client():
    client = Garmin()
    client.garth.loads(GARMIN_TOKENS)
    return client


def get_garmin_activities(client, days_back=DAYS_BACK):
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)
    return client.get_activities_by_date(start_date.isoformat(), end_date.isoformat())


def get_garmin_calendar_workouts(client, days_back=DAYS_BACK):
    """
    Pull Garmin calendar planned workouts for each date.
    """
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)

    calendar_items = []
    day = start_date
    while day <= end_date:
        ds = day.isoformat()
        try:
            payload = client.connectapi(f"/workout-service/schedule/{ds}", method="GET")

            if isinstance(payload, list):
                calendar_items.extend(payload)
            elif isinstance(payload, dict):
                if "calendarItems" in payload and isinstance(payload["calendarItems"], list):
                    calendar_items.extend(payload["calendarItems"])
                else:
                    calendar_items.append(payload)

        except Exception as e:
            print(f"[WARN] Could not fetch Garmin calendar for {ds}: {e}")

        day += timedelta(days=1)

    return calendar_items


def parse_garmin_activity_start(activity):
    raw = activity.get("startTimeGMT") or activity.get("startTimeLocal")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_strava_start(activity):
    return datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def extract_activity_name(g_activity):
    return (
        g_activity.get("activityName")
        or g_activity.get("activityType", {}).get("typeKey")
        or ""
    )


def extract_calendar_name(item):
    return (
        item.get("title")
        or item.get("workoutName")
        or item.get("name")
        or item.get("summary")
        or item.get("workout", {}).get("workoutName")
        or item.get("workout", {}).get("title")
        or ""
    )


def extract_calendar_notes(item):
    parts = []

    def add(value):
        if value is None:
            return
        value = str(value).strip()
        if value and value not in parts:
            parts.append(value)

    add(item.get("notes"))
    add(item.get("description"))
    add(item.get("summary"))

    workout = item.get("workout", {})
    if isinstance(workout, dict):
        add(workout.get("notes"))
        add(workout.get("description"))
        add(workout.get("summary"))

    return "\n".join(parts).strip()


def calendar_item_date_matches(item, target_date):
    blob = str(item)
    return target_date.isoformat() in blob


def find_matching_calendar_workout(g_activity, calendar_items):
    g_start = parse_garmin_activity_start(g_activity)
    if not g_start:
        return None

    g_date = g_start.date()
    g_name = normalize_text(extract_activity_name(g_activity))

    candidates = [item for item in calendar_items if calendar_item_date_matches(item, g_date)]

    if not candidates:
        return None

    # 1. Exact normalized name match on same day
    exact_name_matches = []
    for item in candidates:
        c_name = normalize_text(extract_calendar_name(item))
        if c_name and c_name == g_name:
            exact_name_matches.append(item)

    if len(exact_name_matches) == 1:
        return exact_name_matches[0]
    if len(exact_name_matches) > 1:
        return exact_name_matches[0]

    # 2. Contains match on same day
    fuzzy_matches = []
    for item in candidates:
        c_name = normalize_text(extract_calendar_name(item))
        if c_name and g_name and (c_name in g_name or g_name in c_name):
            fuzzy_matches.append(item)

    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]
    if len(fuzzy_matches) > 1:
        return fuzzy_matches[0]

    return None


def find_matching_garmin_activity_for_strava(strava_activity, garmin_activities):
    s_start = parse_strava_start(strava_activity)
    best = None
    best_delta = timedelta(days=999)

    for g_activity in garmin_activities:
        g_start = parse_garmin_activity_start(g_activity)
        if not g_start:
            continue

        delta = abs(s_start - g_start)
        if delta < best_delta:
            best_delta = delta
            best = g_activity

    # optional safety check
    if best and best_delta.total_seconds() <= MATCH_WINDOW_SECONDS:
        return best

    return None


def main():
    print("== Garmin calendar workout notes -> Strava description ==")

    # Strava
    strava_token = get_strava_access_token()
    strava_activities = get_strava_activities(strava_token)
    print(f"Found {len(strava_activities)} Strava activities")

    # Garmin
    client = get_garmin_client()
    garmin_activities = get_garmin_activities(client)
    calendar_workouts = get_garmin_calendar_workouts(client)

    print(f"Found {len(garmin_activities)} Garmin activities")
    print(f"Found {len(calendar_workouts)} Garmin calendar items")

    updated = 0
    skipped = 0

    for s_act in strava_activities:
        s_name = s_act.get("name", "")
        print(f"\n[STRAVA] {s_name}")

        g_activity = find_matching_garmin_activity_for_strava(s_act, garmin_activities)
        if not g_activity:
            print("  [SKIP] No Garmin completed activity match")
            skipped += 1
            continue

        g_name = extract_activity_name(g_activity)
        print(f"  [GARMIN ACTIVITY] {g_name}")

        calendar_item = find_matching_calendar_workout(g_activity, calendar_workouts)
        if not calendar_item:
            print("  [SKIP] No Garmin calendar workout match")
            skipped += 1
            continue

        cal_name = extract_calendar_name(calendar_item)
        notes = extract_calendar_notes(calendar_item)

        print(f"  [CALENDAR MATCH] {cal_name}")

        if not notes:
            print("  [SKIP] Calendar workout has no notes/description")
            skipped += 1
            continue

        existing_desc = (s_act.get("description") or "").strip()
        if existing_desc == notes.strip():
            print("  [SKIP] Strava already up to date")
            skipped += 1
            continue

        update_strava_description(strava_token, s_act["id"], notes)
        print("  [UPDATED] Strava description updated")
        updated += 1

    print(f"\nDone. Updated: {updated} | Skipped: {skipped}")


if __name__ == "__main__":
    main()