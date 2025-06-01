import os
import json
from flask import Flask, jsonify

app = Flask(__name__)

# Get the path to the JSON file
base_dir = os.path.abspath(os.path.dirname(__file__))
json_path = os.path.join(base_dir, 'matches.json')

# Load the data
data = []  # Initialize as empty list

try:
    with open(json_path, 'r') as file:
        data = json.load(file)
    print(f"✅ Successfully loaded data from {json_path}")
    print(f"📊 Found {len(data)} matches")
except Exception as e:
    print(f"❌ Error loading JSON: {e}")

@app.route('/')
def home():
    return "Brasileirão Broadcast API is running! 🎉 Use /matches endpoint"

@app.route('/matches')
def get_matches():
    if data:
        return jsonify(data)
    else:
        return jsonify({"error": "Data not loaded"}), 500

if __name__ == '__main__':
    app.run(debug=True)