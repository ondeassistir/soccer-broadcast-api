from flask import Flask, jsonify
import pandas as pd
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allows website access

# Load match data
matches = pd.read_csv('matches.csv')

@app.route('/matches', methods=['GET'])
def get_all_matches():
    """Get all matches"""
    return jsonify(matches.to_dict(orient='records'))

@app.route('/matches/<int:week>', methods=['GET'])
def get_week_matches(week):
    """Get matches by week number"""
    week_matches = matches[matches['league_week_number'] == week]
    return jsonify(week_matches.to_dict(orient='records'))

@app.route('/matches/team/<string:team>', methods=['GET'])
def get_team_matches(team):
    """Get matches for specific team (use 3-letter code like FLA)"""
    team = team.upper()
    team_matches = matches[
        (matches['home_team_abbr'] == team) | 
        (matches['away_team_abbr'] == team)
    ]
    return jsonify(team_matches.to_dict(orient='records'))

if __name__ == '__main__':
    app.run(debug=True)