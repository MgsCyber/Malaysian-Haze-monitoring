import os
import psycopg2
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS local_sensor (
            id SERIAL PRIMARY KEY,
            recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            temperature FLOAT,
            humidity FLOAT
        );
    ''')
    conn.commit()
    cursor.close()
    conn.close()

@app.route('/api/sensor', methods=['POST'])
def receive_data():
    data = request.get_json()
    if not data or 'temperature' not in data or 'humidity' not in data:
        return jsonify({"error": "Missing data"}), 400
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO local_sensor (temperature, humidity) VALUES (%s, %s);",
            (data['temperature'], data['humidity'])
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"status": "success"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)