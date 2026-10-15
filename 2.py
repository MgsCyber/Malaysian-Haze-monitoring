import os
import time
import requests
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

# 1. Load credentials
load_dotenv()
TOKEN = os.getenv("AQICN_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DATABASE_URL:
    print("Error: Missing AQICN_TOKEN or DATABASE_URL in .env file.")
    exit(1)

# Bounding box covers Peninsular Malaysia, Sabah, and Sarawak
MALAYSIA_BOUNDS = "0.8,99.5,7.5,119.5"

def get_state_and_district(station_name):
    """
    Strictly filters stations by checking if a Malaysian state or Federal Territory 
    is in the station name. Any station without a matching state is rejected.
    """
    name_upper = station_name.upper()

# Catch Kuala Lumpur edge cases directly
    kl_keywords = ["CHERAS", "KUALA LUMPUR", "WILAYAH PERSEKUTUAN", "KL ", " KL", "BATU MUDA", "BUKIT BINTANG"]
    if any(k in clean_name for k in kl_keywords):
        return "Kuala Lumpur"
    # Strict whitelist of Malaysian states and Federal Territories
    states_list = {
        "JOHOR": "Johor", 
        "KEDAH": "Kedah", 
        "KELANTAN": "Kelantan",
        "MELAKA": "Melaka", 
        "MALACCA": "Melaka", 
        "NEGERI SEMBILAN": "Negeri Sembilan",
        "PAHANG": "Pahang", 
        "PULAU PINANG": "Penang", 
        "PENANG": "Penang",
        "PERAK": "Perak", 
        "PERLIS": "Perlis", 
        "SABAH": "Sabah",
        "SARAWAK": "Sarawak", 
        "SELANGOR": "Selangor", 
        "TERENGGANU": "Terengganu",
        "KUALA LUMPUR": "Kuala Lumpur", 
        "PUTRAJAYA": "Putrajaya", 
        
        "WILAYAH PERSEKUTUAN": "Kuala Lumpur"
    }

    state = None
    for key, correct_name in states_list.items():
        if key in name_upper:
            state = correct_name
            break

    # If it didn't match a Malaysian state exactly, reject it (catches Thai/Indo stations)
    if not state:
        return None, None

    # Extract the district/city (usually the first part before the comma)
    parts = station_name.split(",")
    district = parts[0].strip() if parts else station_name

    return state, district

def collect_and_save_data():
    """Main function that fetches data and saves to the 3 tables."""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{current_time}] Starting data collection...")

    # Fetch Map Data with Retry
    url = f"https://api.waqi.info/map/bounds/?latlng={MALAYSIA_BOUNDS}&token={TOKEN}"
    response = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            break
        except requests.RequestException:
            print(f"Network attempt {attempt + 1} failed. Retrying in 3 seconds...")
            time.sleep(3)

    if not response:
        print(f"[{current_time}] Could not reach API. Aborting this run.")
        return

    result = response.json()
    if result.get("status") != "ok":
        print(f"[{current_time}] API returned an error:", result)
        return

    # Connect to PostgreSQL
    conn = None
    for attempt in range(4):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            break
        except psycopg2.OperationalError:
            print(f"Database wake-up attempt {attempt + 1}/4... waiting 3s")
            time.sleep(3)

    if not conn:
        print(f"[{current_time}] Failed to connect to DB. Aborting this run.")
        return

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS states (
            id SERIAL PRIMARY KEY,
            state_name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stations (
            id SERIAL PRIMARY KEY,
            state_id INTEGER REFERENCES states(id) ON DELETE CASCADE,
            district TEXT,
            station_name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aqi_readings (
            id SERIAL PRIMARY KEY,
            station_id INTEGER REFERENCES stations(id) ON DELETE CASCADE,
            recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            aqi INTEGER
        );
    """)

    saved_count = 0
    skipped_foreign_or_private = 0

    for pin in result.get("data", []):
        raw_station_name = pin.get("station", {}).get("name", "Unknown")
        raw_aqi = pin.get("aqi")

        if not str(raw_aqi).isdigit():
            continue

        state_name, district = get_state_and_district(raw_station_name)

        # Skip Thailand, Indonesia, Singapore, and unclassified private stations
        if state_name is None:
            skipped_foreign_or_private += 1
            continue

        # --- TABLE 1: State ---
        cursor.execute("""
            INSERT INTO states (state_name) 
            VALUES (%s) 
            ON CONFLICT (state_name) DO NOTHING;
        """, (state_name,))
        cursor.execute("SELECT id FROM states WHERE state_name = %s;", (state_name,))
        state_id = cursor.fetchone()[0]

        # --- TABLE 2: Station ---
        cursor.execute("""
            INSERT INTO stations (state_id, district, station_name) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (station_name) DO UPDATE 
            SET state_id = EXCLUDED.state_id, district = EXCLUDED.district;
        """, (state_id, district, raw_station_name))
        cursor.execute("SELECT id FROM stations WHERE station_name = %s;", (raw_station_name,))
        station_id = cursor.fetchone()[0]

        # --- TABLE 3: Reading ---
        cursor.execute("""
            INSERT INTO aqi_readings (station_id, aqi)
            VALUES (%s, %s);
        """, (station_id, int(raw_aqi)))

        saved_count += 1
        tag = f"[{state_name}]"
        print(f"  -> Saved {saved_count} {tag}: {raw_station_name} (AQI: {raw_aqi})")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"[{current_time}] >>> Success! Saved {saved_count} Malaysian stations (Filtered out {skipped_foreign_or_private} foreign/unclassified stations).")

if __name__ == "__main__":
    collect_and_save_data()

    scheduler = BlockingScheduler()
    scheduler.add_job(collect_and_save_data, 'cron', minute=0)

    print("\n[Scheduler Active] The script is running hourly.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped manually.")