// sync_matches.js
// Usage: node sync_matches.js [LEAGUE]
const axios = require('axios');
const { createClient } = require('@supabase/supabase-js');

// Supabase client (SERVICE_ROLE_KEY)
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

// Base URL for your FastAPI service
const API_URL = process.env.API_BASE_URL || 'https://soccer-api-7ykx.onrender.com';

async function syncMatches(league) {
  const url = league
    ? `${API_URL}/data/${league}.json`
    : `${API_URL}/matches`;
  console.log(`Fetching matches from ${url}…`);

  let matches;
  try {
    const resp = await axios.get(url);
    matches = resp.data;
  } catch (e) {
    console.error(`Failed to fetch ${url}:`, e.message);
    process.exit(1);
  }
  
  if (!Array.isArray(matches)) {
    console.error('Invalid response: Expected array of matches');
    process.exit(1);
  }
  
  console.log(`Found ${matches.length} matches to sync.`);

  const rows = matches.map(m => {
    // Handle potential undefined/null values
    const kickoff = m.kickoff ? m.kickoff.toLowerCase().replace(/\s+/g, '_') : '';
    const leagueName = m.league || league || '';
    const lg = leagueName.toLowerCase().replace(/\s+/g, '_');
    
    const home = m.home_team ? m.home_team.toLowerCase().replace(/\s+/g, '_') : '';
    const away = m.away_team ? m.away_team.toLowerCase().replace(/\s+/g, '_') : '';
    
    const computedId = `${lg}_${kickoff}_${home}_x_${away}`;
    const matchId = m.match_id || computedId;

    return {
      match_id:           matchId,
      api_football_id:    m.api_football_id,
      league:             m.league || league,
      league_id:          m.league_id,
      league_week_number: m.league_week_number,
      home_team:          m.home_team,
      home_id:            m.home_id,
      away_team:          m.away_team,
      away_id:            m.away_id,
      kickoff:            m.kickoff,
      broadcasts:         m.broadcasts,
      match_status:       'NS'  // Not Started
    };
  });

  try {
    const { error } = await supabase
      .from('matches')
      .upsert(rows, { onConflict: 'match_id' });

    if (error) throw error;
    
    console.log(`✅ Synced ${rows.length} matches to Supabase!`);
  } catch (error) {
    console.error('Upsert error:', error.message);
    process.exit(1);
  }
}

if (require.main === module) {
  const leagueArg = process.argv[2];
  syncMatches(leagueArg).catch(err => {
    console.error('Sync failed:', err.message || err);
    process.exit(1);
  });
}

module.exports = { syncMatches };