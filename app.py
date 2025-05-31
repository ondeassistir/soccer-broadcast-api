import os
from flask import Flask, jsonify
import pandas as pd

app = Flask(__name__)

# Get the path to the CSV file
base_dir = os.path.abspath(os.path.dirname(__file__))
csv_path = os.path.join(base_dir, 'matches.csv')

# Load the data
try:
    df = pd.read_csv(csv_path)
    print(f"✅ Successfully loaded data from {csv_path}")
    print(f"📊 Found {len(df)} matches")
except Exception as e:
    print(f"❌ Error loading CSV: {e}")
    df = pd.DataFrame()

@app.route('/')
def home():
    return "Brasileirão Broadcast API is running! 🎉 Use /matches endpoint"

@app.route('/matches')
def get_matches():
    if not df.empty:
        return jsonify(df.to_dict(orient='records'))
    else:
        return jsonify({"error": "Data not loaded"}), 500

if __name__ == '__main__':
    app.run(debug=True)