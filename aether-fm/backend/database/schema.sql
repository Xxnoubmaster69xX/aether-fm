-- ============================================================================
-- AetherFM SQLite Database Schema (aether_radio.db)
-- Stores Catalog, Play Telemetry, Contextual Weights, Mood Cards & Herta Memory
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- 1. Master Track Catalog (Synced from Apple Music / Tidal / YouTube)
CREATE TABLE IF NOT EXISTS tracks (
    track_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    source_type TEXT CHECK(source_type IN ('tidal', 'youtube', 'local_cache')) NOT NULL,
    source_id TEXT NOT NULL,
    cover_url TEXT,
    duration_sec REAL DEFAULT 210.0,
    bpm REAL DEFAULT 120.0,
    energy_score REAL DEFAULT 0.6,          -- 0.0 (ambient) to 1.0 (heavy/intense)
    valence_score REAL DEFAULT 0.5,         -- 0.0 (melancholic/dark) to 1.0 (euphoric/upbeat)
    genres JSON DEFAULT '[]',               -- e.g. ["post-hardcore", "j-rock", "electronic"]
    intro_vocal_start_sec REAL DEFAULT 8.0, -- First musical onset/cue-in
    outro_start_sec REAL DEFAULT 195.0,     -- Timestamp to begin crossfade into Song B
    integrated_lufs REAL DEFAULT -14.0,     -- Measured loudness for -14 LUFS normalization
    is_banned INTEGER DEFAULT 0,
    favorite_boost REAL DEFAULT 1.0,
    yt_video_id TEXT,
    resolved_title TEXT,
    resolved_artist TEXT,
    match_score REAL DEFAULT 0.0,
    cooldown_until TIMESTAMP DEFAULT NULL,
    early_skip_count INTEGER DEFAULT 0,
    is_vaulted INTEGER DEFAULT 0,
    musical_key TEXT DEFAULT '8A',
    sub_mood TEXT DEFAULT '',
    first_downbeat_sec REAL DEFAULT 0.0,
    beat_interval_sec REAL DEFAULT 0.5
);

-- 2. Granular Listening & Skip Telemetry
CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT REFERENCES tracks(track_id),
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hour_of_day INTEGER,                    -- 0 to 23
    day_of_week INTEGER,                    -- 0 (Mon) to 6 (Sun)
    active_mood_card_id TEXT,
    listen_duration_sec REAL,
    completion_ratio REAL,                  -- e.g. 0.98 = finished, 0.12 = quick wheel skip
    action_taken TEXT CHECK(action_taken IN ('completed', 'skipped_early', 'skipped_mid', 'requested', 'starred'))
);

-- 3. Learned Contextual Affinity Matrix (Updated after every song)
CREATE TABLE IF NOT EXISTS context_weights (
    genre TEXT,
    hour_bucket TEXT,                       -- 'morning' (6-11), 'afternoon' (12-17), 'evening' (18-22), 'late_night' (23-5)
    affinity_score REAL DEFAULT 1.0,
    consecutive_skips INTEGER DEFAULT 0,
    PRIMARY KEY (genre, hour_bucket)
);

-- 4. Procedurally Unlocked & Pinned Mood Cards
CREATE TABLE IF NOT EXISTS mood_cards (
    card_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,                    -- e.g. "Simulated Overdrive #83"
    subtitle TEXT,                          -- e.g. "135-175 BPM • High-Velocity Rock & Electro"
    color_hex_primary TEXT DEFAULT '#7B2CBF',
    color_hex_secondary TEXT DEFAULT '#240046',
    icon_name TEXT DEFAULT 'auto_awesome',
    target_genres JSON DEFAULT '[]',
    min_bpm REAL DEFAULT 60.0,
    max_bpm REAL DEFAULT 200.0,
    min_energy REAL DEFAULT 0.0,
    max_energy REAL DEFAULT 1.0,
    crossfade_style TEXT DEFAULT 'beat_blend_8s', -- 'quick_cut_4s', 'beat_blend_8s', 'long_filter_12s'
    dj_persona_prompt TEXT,                 -- Specific tone instructions for Madam Herta when active
    is_pinned_to_car_wheel INTEGER DEFAULT 1,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Rolling DJ Memory (Prevents Madam Herta from repeating jokes or tracks)
CREATE TABLE IF NOT EXISTS dj_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spoken_text TEXT NOT NULL,
    emotion_tag TEXT DEFAULT 'aloof',
    referenced_tracks JSON DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Persistent Rotation History (Prevents track repeats across restarts)
CREATE TABLE IF NOT EXISTS session_rotation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Seed 6 Core Genre Stations + Full Spectrum Flow
-- ============================================================================
INSERT OR IGNORE INTO mood_cards (
    card_id, title, subtitle, color_hex_primary, color_hex_secondary, icon_name,
    target_genres, min_bpm, max_bpm, min_energy, max_energy, crossfade_style,
    dj_persona_prompt, is_pinned_to_car_wheel
) VALUES
(
    'station_rock',
    'CH-01 // ROCK & METAL',
    'Alternative, Post-Hardcore, Metalcore, J-Rock & Punk',
    '#E63946', '#111111', 'electric_bolt',
    '["rock", "j-rock", "post-hardcore", "metal", "punk", "alternative"]',
    70.0, 205.0, 0.4, 1.0, 'quick_cut_4s',
    'Tight phrase cuts, maximum punch and driving guitar/drum presence.',
    1
),
(
    'station_country',
    'CH-02 // COUNTRY & HEARTLAND',
    'Modern Country, Americana, Southern Rock & Folk',
    '#E76F51', '#1A1412', 'grass',
    '["country", "americana", "folk", "southern rock", "acoustic"]',
    65.0, 150.0, 0.2, 0.9, 'warm_acoustic_5s',
    'Warm acoustic guitar fades with smooth harmonic transitions.',
    1
),
(
    'station_jpop',
    'CH-03 // J-POP & TOKYO GROOVE',
    'Modern J-Pop, Anime Pop, Late-Night Tokyo Groove & Vocaloid',
    '#FF4081', '#1F0018', 'auto_awesome',
    '["j-pop", "anime", "pop", "tokyo groove", "utaite"]',
    80.0, 195.0, 0.3, 1.0, 'harmonic_blend_6s',
    'High-velocity vocal transitions and dynamic melody crossovers.',
    1
),
(
    'station_jcore',
    'CH-04 // J-CORE & EUROBEAT',
    'J-Core, Hardcore Tano*C, Denonbu & Super Eurobeat Overdrive',
    '#FFEA00', '#1A1A00', 'speed',
    '["j-core", "eurobeat", "hard dance", "happy hardcore", "speedcore"]',
    130.0, 210.0, 0.6, 1.0, 'rave_bass_swap_8s',
    'High-velocity rave bass-swaps with zero kick drum phase cancellation.',
    1
),
(
    'station_edm',
    'CH-05 // EDM & FESTIVAL BASS',
    'Progressive House, Bass House, Trance, DnB & Future Bass',
    '#00E5FF', '#001E28', 'graphic_eq',
    '["electronic", "dance", "house", "bass house", "edm", "future bass", "drum and bass", "synthwave"]',
    115.0, 180.0, 0.4, 1.0, 'club_eq_crossover_9s',
    'Club-standard 8-bar isolator EQ blend and low-end crossover sweep.',
    1
),
(
    'station_game_ost',
    'CH-06 // GAME OST & BOSS THEMES',
    'Video Game Soundtracks, Boss Themes & Orchestral/Electronic Scores',
    '#7C4DFF', '#12002B', 'videogame_asset',
    '["ost", "soundtrack", "cinematic", "game", "ambient"]',
    60.0, 195.0, 0.1, 1.0, 'cinematic_swell_7s',
    'Cinematic tail crossfades and dynamic orchestral layering.',
    1
),
(
    'station_full_spectrum',
    'CH-07 // ALL-STATION FLOW',
    'Smart Curated Mix across All 6 Stations based on Mood',
    '#FFFFFF', '#141414', 'hub',
    '["rock", "j-rock", "country", "j-pop", "j-core", "eurobeat", "electronic", "dance", "ost"]',
    60.0, 210.0, 0.0, 1.0, 'club_eq_crossover_9s',
    'Seamless cross-genre DJ mixing guided by the live mood vector.',
    1
);
