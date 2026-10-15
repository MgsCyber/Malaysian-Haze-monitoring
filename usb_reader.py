import serial
import requests
import time

# Update 'COM3' to match your Arduino's port in the Arduino IDE
ARDUINO_PORT = 'COM3' 
API_URL = "http://localhost:5000/api/sensor"

try:
    arduino = serial.Serial(ARDUINO_PORT, 9600, timeout=1)
    time.sleep(2) # Allow Arduino to reset upon connection
    print(f"Listening to Arduino on {ARDUINO_PORT}...")
except Exception as e:
    print(f"Could not connect to {ARDUINO_PORT}. Is the Arduino plugged in?")
    exit()

while True:
    try:
        # Read the line printed by the Arduino
        raw_data = arduino.readline().decode('utf-8').strip()
        
        if raw_data and raw_data != "Error":
            temp, hum = raw_data.split(',')
            
            # Format and send to your Docker API
            payload = {"temperature": float(temp), "humidity": float(hum)}
            response = requests.post(API_URL, json=payload)
            
            print(f"Forwarded {payload} | Database API Response: {response.status_code}")
            
    except Exception as e:
        print(f"Error reading or sending data: {e}")
        time.sleep(2)