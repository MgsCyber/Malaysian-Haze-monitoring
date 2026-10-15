import os
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine
import datetime
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Malaysia Haze Dashboard", layout="wide", page_icon="🇲🇾")
st.title("🇲🇾 National Haze & IoT Climate Dashboard")

TOKEN = os.getenv("AQICN_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
MALAYSIA_BOUNDS = "0.8,99.5,7.5,119.5"

# --- DATABASE CONNECTION (Cached Persistent Pool) ---
@st.cache_resource
def get_db_engine():
    """Keeps the connection pool open to prevent heavy reconnection overhead."""
    return create_engine(DATABASE_URL, pool_pre_ping=True)

db_engine = get_db_engine()

# --- HELPER FUNCTIONS ---

def get_state_name(station_name):
    """
    Matches station to Malaysian State/Federal Territory.
    Explicitly filters out foreign stations and catches edge cases like Cheras.
    """
    name_upper = station_name.upper()
    clean_name = " ".join(name_upper.split())

    # Reject foreign keywords
    foreign_keywords = [
        "THAILAND", "SINGAPORE", "INDONESIA", "BATAM", 
        "SONGKHLA", "HAT YAI", "YALA", "NARATHIWAT", "BETONG", "TRANG"
    ]
    if any(k in clean_name for k in foreign_keywords):
        return None

    # Catch Kuala Lumpur edge cases directly
    if "CHERAS" in clean_name or "KUALA LUMPUR" in clean_name or "WILAYAH PERSEKUTUAN" in clean_name:
        return "Kuala Lumpur"

    states_list = {
        "JOHOR": "Johor", "KEDAH": "Kedah", "KELANTAN": "Kelantan",
        "MELAKA": "Melaka", "MALACCA": "Melaka", "NEGERI SEMBILAN": "Negeri Sembilan",
        "PAHANG": "Pahang", "PULAU PINANG": "Penang", "PENANG": "Penang",
        "PERAK": "Perak", "PERLIS": "Perlis", "SABAH": "Sabah",
        "SARAWAK": "Sarawak", "SELANGOR": "Selangor", "TERENGGANU": "Terengganu",
        "PUTRAJAYA": "Putrajaya", "LABUAN": "Labuan",
        "BATU MUDA": "Kuala Lumpur", "KUALA LUMPUR": "Kuala Lumpur", "PETALING JAYA": "Selangor", "SHAH ALAM": "Selangor",
        "KLANG": "Selangor", "BANTING": "Selangor", "NILAI": "Negeri Sembilan",
        "SEREMBAN": "Negeri Sembilan", "IPOH": "Perak", "KUCHING": "Sarawak", "KOTA KINABALU": "Sabah"
    }
    
    for key, correct_name in states_list.items():
        if key in clean_name:
            return correct_name

    return None

@st.cache_data(ttl=300)
def fetch_live_map_data():
    """Fetches live map data and strictly discards non-Malaysian stations."""
    url = f"https://api.waqi.info/map/bounds/?latlng={MALAYSIA_BOUNDS}&token={TOKEN}"
    response = requests.get(url).json()
    
    map_data = []
    for pin in response.get("data", []):
        raw_aqi = pin.get("aqi")
        if not str(raw_aqi).isdigit():
            continue
            
        station_name = pin.get("station", {}).get("name", "Unknown")
        state = get_state_name(station_name)
        
        # Discard station if no Malaysian state match is found
        if state is None:
            continue
        
        map_data.append({
            "Station": station_name,
            "State": state,
            "AQI": int(raw_aqi),
            "lat": pin.get("lat"),
            "lon": pin.get("lon")
        })
    return pd.DataFrame(map_data)

def fetch_station_history(station_name):
    """Fetches the past 24 hours of data from PostgreSQL."""
    query = """
        SELECT 
            r.recorded_at AT TIME ZONE 'Asia/Kuala_Lumpur' AS time, 
            r.aqi,
            r.temperature,
            r.wind
        FROM aqi_readings r
        JOIN stations s ON r.station_id = s.id
        WHERE s.station_name = %(station_name)s
        AND r.recorded_at >= NOW() - INTERVAL '24 HOURS'
        ORDER BY r.recorded_at ASC
    """
    try:
        with db_engine.connect() as conn:
            return pd.read_sql_query(query, conn, params={"station_name": station_name})
    except Exception:
        query_fallback = """
            SELECT 
                r.recorded_at AT TIME ZONE 'Asia/Kuala_Lumpur' AS time, 
                r.aqi 
            FROM aqi_readings r
            JOIN stations s ON r.station_id = s.id
            WHERE s.station_name = %(station_name)s
            AND r.recorded_at >= NOW() - INTERVAL '24 HOURS'
            ORDER BY r.recorded_at ASC
        """
        with db_engine.connect() as conn:
            return pd.read_sql_query(query_fallback, conn, params={"station_name": station_name})

def fetch_latest_local_sensor():
    """Fetches the latest reading instantly via the persistent connection pool."""
    query = """
        SELECT temperature, humidity, recorded_at 
        FROM local_sensor 
        ORDER BY recorded_at DESC 
        LIMIT 1
    """
    try:
        with db_engine.connect() as conn:
            df = pd.read_sql_query(query, conn)
        if not df.empty:
            return df.iloc[0]
    except Exception as e:
        st.error(f"Error loading sensor data: {e}")
    return None

# --- AUTO-REFRESHING METRICS FRAGMENT ---
@st.fragment(run_every=5)
def render_top_metrics(df_map):
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    col1.metric("National Average AQI", f"{df_map['AQI'].mean():.0f}")
    
    worst_row = df_map.loc[df_map['AQI'].idxmax()]
    col2.metric("Worst Air Today", f"{worst_row['AQI']} AQI", delta=worst_row['Station'], delta_color="inverse")
    
    best_row = df_map.loc[df_map['AQI'].idxmin()]
    col3.metric("Cleanest Air Today", f"{best_row['AQI']} AQI", delta=best_row['Station'], delta_color="normal")
    
    col4.metric("Active Stations", len(df_map))
    
    sensor_latest = fetch_latest_local_sensor()
    current_time = datetime.datetime.now().strftime("%H:%M:%S")
    
    if sensor_latest is not None:
        col5.metric("Local Temp", f"{sensor_latest['temperature']:.1f} °C")
        col6.metric("Local Humidity", f"{sensor_latest['humidity']:.1f}%")
        st.caption(f"🟢 Sensor updated at: {current_time}")
    else:
        col5.metric("Local Temp", "N/A")
        col6.metric("Local Humidity", "N/A")
        st.caption(f"🔴 No data found. Checked at: {current_time}")

# --- DASHBOARD UI ---

df_map = fetch_live_map_data()

if df_map.empty:
    st.error("Could not load map data. Please check your API token.")
else:
    st.markdown("### Real-Time National Overview")
    
    # Auto-refreshes every 5 seconds without reloading the rest of the page
    render_top_metrics(df_map)
    
    st.divider()

    # --- State Filter ---
    states_available = ["All Malaysia"] + sorted(df_map["State"].unique().tolist())
    selected_state = st.selectbox("🗺️ Select a State / Category to Highlight:", states_available)

    STATE_CENTERS = {
        "Johor": {"lat": 2.03, "lon": 103.31, "zoom": 7.5},
        "Kedah": {"lat": 6.11, "lon": 100.36, "zoom": 8},
        "Kelantan": {"lat": 5.25, "lon": 102.24, "zoom": 7.5},
        "Melaka": {"lat": 2.18, "lon": 102.25, "zoom": 9},
        "Negeri Sembilan": {"lat": 2.72, "lon": 101.93, "zoom": 8},
        "Pahang": {"lat": 3.81, "lon": 103.32, "zoom": 7},
        "Penang": {"lat": 5.41, "lon": 100.32, "zoom": 9.5},
        "Perak": {"lat": 4.59, "lon": 101.09, "zoom": 7.5},
        "Perlis": {"lat": 6.44, "lon": 100.19, "zoom": 10},
        "Sabah": {"lat": 5.42, "lon": 116.87, "zoom": 6.5},
        "Sarawak": {"lat": 2.55, "lon": 113.00, "zoom": 6},
        "Selangor": {"lat": 3.07, "lon": 101.51, "zoom": 8.5},
        "Terengganu": {"lat": 5.31, "lon": 103.13, "zoom": 7.5},
        "Kuala Lumpur": {"lat": 3.14, "lon": 101.68, "zoom": 10.5},
        "Putrajaya": {"lat": 2.92, "lon": 101.69, "zoom": 11},
        "Labuan": {"lat": 5.28, "lon": 115.24, "zoom": 11},
        "All Malaysia": {"lat": 4.21, "lon": 108.97, "zoom": 5}
    }

    if selected_state == "All Malaysia":
        filtered_map_df = df_map.reset_index(drop=True)
        center_coords = STATE_CENTERS["All Malaysia"]
    else:
        filtered_map_df = df_map[df_map["State"] == selected_state].reset_index(drop=True)
        center_coords = STATE_CENTERS.get(selected_state, STATE_CENTERS["All Malaysia"])

    # --- Layout: 70% Map, 30% Details Box ---
    col_map, col_details = st.columns([7, 3])

    with col_map:
        st.subheader(f"Air Quality Map: {selected_state}")
        
        fig_map = px.scatter_map(
            filtered_map_df, 
            lat="lat", lon="lon", 
            color="AQI", 
            size="AQI", 
            hover_name="Station",
            color_continuous_scale=[
                (0.0, "green"), (0.208, "yellow"), (0.417, "orange"), 
                (0.625, "red"), (0.833, "purple"), (1.0, "#7e0023")
            ], 
            range_color=[0, 240],
            size_max=20, 
            zoom=center_coords["zoom"], 
            center={"lat": center_coords["lat"], "lon": center_coords["lon"]},
            map_style="open-street-map"
        )
        
        fig_map.update_coloraxes(
            colorbar=dict(
                tickmode="array",
                tickvals=[0, 50, 100, 150, 200, 240]
            )
        )
        fig_map.update_layout(margin={"r":0,"t":0,"l":0,"b":0})
        
        map_event = st.plotly_chart(
            fig_map, 
            use_container_width=True, 
            on_select="rerun",
            key="aqi_map"
        )

    with col_details:
        st.subheader("📍 Station Insights")
        
        selected_indices = map_event.selection.point_indices if hasattr(map_event, 'selection') else []
        
        if selected_indices:
            clicked_index = selected_indices[0]
            station_data = filtered_map_df.iloc[clicked_index]
            station_name = station_data['Station']
            aqi_val = station_data['AQI']
            
            st.markdown(f"### {station_name}")
            st.markdown(f"**State / Region:** {station_data['State']}")
            
            if aqi_val <= 50:
                st.success(f"**AQI: {aqi_val} (Good)** \n\nAir quality is healthy.")
            elif aqi_val <= 100:
                st.warning(f"**AQI: {aqi_val} (Moderate)** \n\nAcceptable for general public.")
            elif aqi_val <= 200:
                st.error(f"**AQI: {aqi_val} (Unhealthy)** \n\nReduce prolonged outdoor activities.")
            else:
                st.error(f"**AQI: {aqi_val} (Hazardous)** \n\nRemain indoors. Wear N95 masks.")
            
            history_df = fetch_station_history(station_name)
            
            if not history_df.empty:
                latest_db_record = history_df.iloc[-1]
                
                if 'temperature' in latest_db_record and pd.notna(latest_db_record['temperature']):
                    st.write(f"🌡️ **Temperature:** {latest_db_record['temperature']} °C")
                if 'wind' in latest_db_record and pd.notna(latest_db_record['wind']):
                    st.write(f"💨 **Wind:** {latest_db_record['wind']} km/h")
                    
                csv_data = history_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export Station History (CSV)",
                    data=csv_data,
                    file_name=f"{station_name}_history.csv",
                    mime="text/csv",
                )
            else:
                st.info("No saved database history for this location yet.")
                
        else:
            st.info("👆 **Click on any map point** to view detailed health advisories, live weather, and export data.")

    st.divider()

    # --- 24-Hour Station Analysis ---
    st.subheader("📊 24-Hour Historical Trend")
    stations_in_state = filtered_map_df["Station"].tolist()
    
    if stations_in_state:
        default_index = 0
        if 'selected_indices' in locals() and selected_indices:
            clicked_name = filtered_map_df.iloc[selected_indices[0]]['Station']
            if clicked_name in stations_in_state:
                default_index = stations_in_state.index(clicked_name)

        selected_station = st.selectbox("Select a Station to view history:", stations_in_state, index=default_index)
        history_df = fetch_station_history(selected_station)

        if not history_df.empty:
            col_line, col_bar = st.columns(2)
            
            with col_line:
                fig_line = px.line(
                    history_df, x="time", y="aqi", 
                    markers=True, 
                    title=f"AQI Trend: {selected_station}",
                    labels={"time": "Time (MYT)", "aqi": "Air Quality Index"}
                )
                fig_line.update_traces(line_color='#FF4B4B')
                st.plotly_chart(fig_line, use_container_width=True)
                
            with col_bar:
                history_df['Health Category'] = pd.cut(
                    history_df['aqi'], 
                    bins=[-1, 50, 100, 150, 200, 300, 999], 
                    labels=[
                        'Good (0-50)', 
                        'Moderate (51-100)', 
                        'Unhealthy for Sensitive (101-150)', 
                        'Unhealthy (151-200)', 
                        'Very Unhealthy (201-300)', 
                        'Hazardous (301+)'
                    ]
                )
                
                health_colors = {
                    'Good (0-50)': 'green',
                    'Moderate (51-100)': '#FFD700',
                    'Unhealthy for Sensitive (101-150)': 'orange',
                    'Unhealthy (151-200)': 'red',
                    'Very Unhealthy (201-300)': 'purple',
                    'Hazardous (301+)': '#800000'
                }
                
                fig_bar = px.bar(
                    history_df, x="time", y="aqi", 
                    color="Health Category",
                    color_discrete_map=health_colors,
                    title="AQI Levels by Health Category",
                    labels={"time": "Time (MYT)", "aqi": "Air Quality Index"}
                )
                
                fig_bar.update_layout(
                    legend_title_text='Category',
                    xaxis_title="Time (MYT)",
                    yaxis_title="Air Quality Index"
                )
                st.plotly_chart(fig_bar, use_container_width=True)
                
        else:
            st.info("No historical data found in the database for this station yet.")
    else:
        st.warning("No stations found in this category currently.")