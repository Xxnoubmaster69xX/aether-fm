"""
Async SQLite Manager, 6-Genre Stations & Mood-Reading Candidate Selector (db_manager.py)
Features:
1. 6 Dedicated Genre Stations (Rock, Country, J-Pop, J-Core, EDM, Game OST) + Full Spectrum Flow.
2. Strict <50% Skip Day-Ban & Vaulting Engine:
   - Early skip (<50%): 24-hour day-ban + negative mood vector shift.
   - Repeated early skip (>=2 times): 14-day vault lock + affinity suppression.
3. Camelot Key Harmonic Matching (+0.35 score bonus for compatible keys).
4. Rolling 5-track Session Mood Vector to seamlessly ride and read listener vibe.
"""

from datetime import datetime
import json
import random
from typing import Any, Dict, List, Optional
import aiosqlite

from audio_engine.beat_analyzer import are_keys_compatible
from config import DB_PATH, SCHEMA_PATH


def get_hour_bucket(hour: int) -> str:
    if 6 <= hour <= 11:
        return "morning"
    elif 12 <= hour <= 17:
        return "afternoon"
    elif 18 <= hour <= 22:
        return "evening"
    return "late_night"


def normalize_primary_artist(artist: str) -> str:
    raw = (artist or "").strip().lower()
    for sep in (" & ", " feat.", " ft.", ",", " x ", " with "):
        if sep in raw:
            raw = raw.split(sep, 1)[0].strip()
    return raw


def compute_track_mood(track: Dict[str, Any]) -> str:
    artist_raw = (track.get("artist") or "").lower()
    genres = [g.lower() for g in (track.get("genres") or [])]
    bpm = float(track.get("bpm") or 128.0)

    if any(g in genres for g in ("eurobeat",)) or any(k in artist_raw for k in ("dave rodgers", "fastway", "manuel", "max coveri", "m.o.v.e")):
        return "SUPER EUROBEAT OVERDRIVE"
    if any(g in genres for g in ("j-core", "hardcore")) or any(k in artist_raw for k in ("denonbu", "srav3r", "usao", "t+pazolite", "camellia", "giga", "teddyloid")):
        return "TOKYO HYPER J-CORE"
    if any(g in genres for g in ("metal", "post-hardcore", "punk")) or any(k in artist_raw for k in ("linkin park", "coldrain", "one ok rock", "skillet", "band-maid", "avenged sevenfold")):
        return "HIGH-GAIN ROCK & METAL"
    if any(g in genres for g in ("country", "americana", "folk")) or any(k in artist_raw for k in ("morgan wallen", "luke combs", "zach bryan", "chris stapleton")):
        return "HEARTLAND COUNTRY ROADS"
    if any(g in genres for g in ("house", "edm", "bass house", "dance")) or any(k in artist_raw for k in ("martin garrix", "porter robinson", "avicii", "madeon", "knock2", "odesza", "kygo")):
        return "FESTIVAL BASS & CLUB EDM"
    if any(g in genres for g in ("ost", "soundtrack")) or any(k in artist_raw for k in ("hoyo-mix", "masayoshi soken", "casey edwards", "nobuo uematsu")):
        return "CINEMATIC GAME OST"
    if any(k in artist_raw for k in ("natori", "vaundy", "zutomayo", "yama", "eve")):
        return "LATE-NIGHT TOKYO GROOVE"
    return "STELLAR HIGH-VELOCITY POP"


class DatabaseManager:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        # Rolling session mood vector: maintains live target preferences
        self.session_target_bpm: float = 130.0
        self.session_target_energy: float = 0.65
        self.session_last_key: str = "8A"
        self.recent_early_skipped_moods: List[str] = []

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            await db.executescript(schema_sql)

            # Auto-migrate new columns into tracks if not present
            async with db.execute("PRAGMA table_info(tracks)") as cur:
                existing_cols = {row[1] for row in await cur.fetchall()}
            for col, col_type in [
                ("yt_video_id", "TEXT"),
                ("resolved_title", "TEXT"),
                ("resolved_artist", "TEXT"),
                ("match_score", "REAL DEFAULT 0.0"),
                ("cooldown_until", "TIMESTAMP DEFAULT NULL"),
                ("early_skip_count", "INTEGER DEFAULT 0"),
                ("is_vaulted", "INTEGER DEFAULT 0"),
                ("musical_key", "TEXT DEFAULT '8A'"),
                ("sub_mood", "TEXT DEFAULT ''"),
                ("first_downbeat_sec", "REAL DEFAULT 0.0"),
                ("beat_interval_sec", "REAL DEFAULT 0.5"),
            ]:
                if col not in existing_cols:
                    try:
                        await db.execute(f"ALTER TABLE tracks ADD COLUMN {col} {col_type}")
                    except Exception:
                        pass

            # Seed the 6 core stations + full spectrum
            stations = [
                (
                    "station_rock",
                    "CH-01 // ROCK & METAL",
                    "Alternative, Post-Hardcore, Metalcore, J-Rock & Punk",
                    "#E63946", "#111111", "electric_bolt",
                    json.dumps(["rock", "j-rock", "post-hardcore", "metal", "punk", "alternative"]),
                    70.0, 205.0, 0.4, 1.0, "quick_cut_4s",
                    "Driving guitar-led cuts and tight punchy transitions.", 1
                ),
                (
                    "station_country",
                    "CH-02 // COUNTRY & HEARTLAND",
                    "Modern Country, Americana, Southern Rock & Folk",
                    "#E76F51", "#1A1412", "grass",
                    json.dumps(["country", "americana", "folk", "southern rock", "acoustic"]),
                    65.0, 150.0, 0.2, 0.9, "warm_acoustic_5s",
                    "Warm acoustic fades and heartland roots.", 1
                ),
                (
                    "station_jpop",
                    "CH-03 // J-POP & TOKYO GROOVE",
                    "Modern J-Pop, Anime Pop, Late-Night Tokyo Groove & Vocaloid",
                    "#FF4081", "#1F0018", "auto_awesome",
                    json.dumps(["j-pop", "anime", "pop", "tokyo groove", "utaite"]),
                    80.0, 195.0, 0.3, 1.0, "harmonic_blend_6s",
                    "High-velocity vocal transitions and modern Tokyo hooks.", 1
                ),
                (
                    "station_jcore",
                    "CH-04 // J-CORE & EUROBEAT",
                    "J-Core, Hardcore Tano*C, Denonbu & Super Eurobeat Overdrive",
                    "#FFEA00", "#1A1A00", "speed",
                    json.dumps(["j-core", "eurobeat", "hard dance", "happy hardcore", "speedcore"]),
                    130.0, 210.0, 0.6, 1.0, "rave_bass_swap_8s",
                    "High-velocity rave bass-swaps with zero kick cancellation.", 1
                ),
                (
                    "station_edm",
                    "CH-05 // EDM & FESTIVAL BASS",
                    "Progressive House, Bass House, Trance, DnB & Future Bass",
                    "#00E5FF", "#001E28", "graphic_eq",
                    json.dumps(["electronic", "dance", "house", "bass house", "edm", "future bass", "drum and bass"]),
                    115.0, 180.0, 0.4, 1.0, "club_eq_crossover_9s",
                    "Club-standard 8-bar isolator EQ blend and low-end sweep.", 1
                ),
                (
                    "station_game_ost",
                    "CH-06 // GAME OST & BOSS THEMES",
                    "Video Game Soundtracks, Boss Themes & Cinematic Scores",
                    "#7C4DFF", "#12002B", "videogame_asset",
                    json.dumps(["ost", "soundtrack", "cinematic", "game", "ambient"]),
                    60.0, 195.0, 0.1, 1.0, "cinematic_swell_7s",
                    "Cinematic tail crossfades and orchestral layering.", 1
                ),
                (
                    "station_full_spectrum",
                    "CH-07 // ALL-STATION FLOW",
                    "Smart Curated Mix across All 6 Stations based on Mood",
                    "#FFFFFF", "#141414", "hub",
                    json.dumps(["rock", "j-rock", "country", "j-pop", "j-core", "eurobeat", "electronic", "dance", "ost"]),
                    60.0, 210.0, 0.0, 1.0, "club_eq_crossover_9s",
                    "Seamless cross-genre DJ mixing guided by live mood.", 1
                ),
            ]

            for st in stations:
                await db.execute(
                    """
                    INSERT INTO mood_cards (
                        card_id, title, subtitle, color_hex_primary, color_hex_secondary,
                        icon_name, target_genres, min_bpm, max_bpm, min_energy, max_energy,
                        crossfade_style, dj_persona_prompt, is_pinned_to_car_wheel
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(card_id) DO UPDATE SET
                        title = excluded.title,
                        subtitle = excluded.subtitle,
                        color_hex_primary = excluded.color_hex_primary,
                        color_hex_secondary = excluded.color_hex_secondary,
                        icon_name = excluded.icon_name,
                        target_genres = excluded.target_genres,
                        crossfade_style = excluded.crossfade_style
                    """,
                    st,
                )

            # Ensure all context weights are within sane bounds
            await db.execute(
                "UPDATE context_weights SET affinity_score = 1.0 WHERE affinity_score > 1.4 OR affinity_score < 0.6"
            )
            await db.commit()

    async def upsert_track(self, track: Dict[str, Any]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO tracks (
                    track_id, title, artist, album, source_type, source_id,
                    cover_url, duration_sec, bpm, energy_score, valence_score,
                    genres, intro_vocal_start_sec, outro_start_sec, integrated_lufs,
                    yt_video_id, resolved_title, resolved_artist, match_score,
                    musical_key, sub_mood, first_downbeat_sec, beat_interval_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    cover_url = CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE tracks.cover_url END,
                    bpm = excluded.bpm,
                    energy_score = excluded.energy_score,
                    intro_vocal_start_sec = excluded.intro_vocal_start_sec,
                    outro_start_sec = excluded.outro_start_sec,
                    integrated_lufs = excluded.integrated_lufs,
                    musical_key = excluded.musical_key,
                    sub_mood = excluded.sub_mood,
                    first_downbeat_sec = excluded.first_downbeat_sec,
                    beat_interval_sec = excluded.beat_interval_sec,
                    yt_video_id = CASE WHEN excluded.yt_video_id IS NOT NULL AND excluded.yt_video_id != '' THEN excluded.yt_video_id ELSE tracks.yt_video_id END,
                    resolved_title = CASE WHEN excluded.resolved_title IS NOT NULL AND excluded.resolved_title != '' THEN excluded.resolved_title ELSE tracks.resolved_title END,
                    resolved_artist = CASE WHEN excluded.resolved_artist IS NOT NULL AND excluded.resolved_artist != '' THEN excluded.resolved_artist ELSE tracks.resolved_artist END,
                    match_score = CASE WHEN excluded.match_score > 0 THEN excluded.match_score ELSE tracks.match_score END
                """,
                (
                    track["track_id"],
                    track["title"],
                    track["artist"],
                    track.get("album", ""),
                    track["source_type"],
                    track["source_id"],
                    track.get("cover_url", ""),
                    track.get("duration_sec", 210.0),
                    track.get("bpm", 120.0),
                    track.get("energy_score", 0.6),
                    track.get("valence_score", 0.5),
                    json.dumps(track.get("genres", [])),
                    track.get("intro_vocal_start_sec", 0.0),
                    track.get("outro_start_sec", 195.0),
                    track.get("integrated_lufs", -14.0),
                    track.get("yt_video_id", None),
                    track.get("resolved_title", None),
                    track.get("resolved_artist", None),
                    float(track.get("match_score", 0.0)),
                    track.get("musical_key", "8A"),
                    track.get("sub_mood", ""),
                    float(track.get("first_downbeat_sec", 0.0) or 0.0),
                    float(track.get("beat_interval_sec", 0.5) or 0.5),
                ),
            )
            await db.commit()

    async def get_top_candidates(
        self,
        active_card_id: str,
        current_bpm: float = 125.0,
        limit: int = 8,
        exclude_ids: Optional[List[str]] = None,
        exclude_artists: Optional[List[str]] = None,
        preferred_mood: Optional[str] = None,
        disliked_moods: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Stage 1 Mood-Reading Candidate Selector:
        - Filters out tracks on cooldown (<50% skip 24h ban) or vaulted.
        - Prioritizes target genres of the active station.
        - Applies Camelot key compatibility bonuses (+0.35) and BPM flow continuity.
        - Anti-clusters skipped artists and moods.
        """
        now = datetime.now()
        hour_bucket = get_hour_bucket(now.hour)
        excluded_set = set(exclude_ids or [])
        excluded_artist_set = {
            normalize_primary_artist(a) for a in (exclude_artists or []) if a and a.strip()
        }
        disliked_mood_set = {m.upper() for m in (disliked_moods or []) if m}
        pref_mood_upper = (preferred_mood or "").strip().upper()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # 1. Fetch active station rules
            async with db.execute(
                "SELECT * FROM mood_cards WHERE card_id = ?", (active_card_id,)
            ) as cur:
                card = await cur.fetchone()

            target_genres = json.loads(card["target_genres"]) if card and card["target_genres"] else []

            # 2. Query unbanned, un-vaulted tracks not currently in cooldown
            query = """
                SELECT t.*,
                       COALESCE(AVG(ph.completion_ratio), 0.75) AS avg_completion
                FROM tracks t
                LEFT JOIN play_history ph ON t.track_id = ph.track_id
                WHERE t.is_banned = 0
                  AND t.is_vaulted = 0
                  AND (t.cooldown_until IS NULL OR t.cooldown_until <= datetime('now'))
                  AND t.track_id NOT IN (
                      SELECT track_id FROM play_history
                      WHERE played_at >= datetime('now', '-75 minutes')
                  )
                GROUP BY t.track_id
            """
            async with db.execute(query) as cur:
                rows = await cur.fetchall()

            rows = [r for r in rows if r["track_id"] not in excluded_set]

            # Primary artist deduplication filter
            artist_filtered = [
                r for r in rows if normalize_primary_artist(r["artist"]) not in excluded_artist_set
            ]
            if len(artist_filtered) >= 3:
                rows = artist_filtered

            # Fallback if catalog is heavily on cooldown
            if len(rows) < 3:
                async with db.execute(
                    """
                    SELECT t.*, COALESCE(AVG(ph.completion_ratio), 0.75) AS avg_completion
                    FROM tracks t
                    LEFT JOIN play_history ph ON t.track_id = ph.track_id
                    WHERE t.is_banned = 0 AND t.is_vaulted = 0
                    GROUP BY t.track_id
                    """
                ) as cur:
                    fallback_rows = await cur.fetchall()
                rows = [r for r in fallback_rows if r["track_id"] not in excluded_set] or fallback_rows

            scored_candidates = []
            for row in rows:
                track_genres = json.loads(row["genres"] or "[]")
                cand_genres_set = set(g.lower() for g in track_genres)

                # Station genre alignment score
                if target_genres:
                    matches = len(cand_genres_set.intersection(set(g.lower() for g in target_genres)))
                    genre_score = 1.0 if matches > 0 else 0.1
                else:
                    genre_score = 0.8

                # BPM continuity
                bpm_diff = min(
                    abs(row["bpm"] - current_bpm),
                    abs(row["bpm"] * 2 - current_bpm),
                    abs(row["bpm"] * 0.5 - current_bpm),
                )
                b_bpm_match = max(0.0, 1.0 - (bpm_diff / 45.0))

                # Camelot Key Compatibility Bonus (+0.35)
                key_bonus = 0.35 if are_keys_compatible(str(row["musical_key"] or "8A"), self.session_last_key) else 0.0

                # Completion ratio affinity
                r_completion = float(row["avg_completion"])

                # Controlled discovery jitter
                eps_discovery = random.uniform(0.0, 0.45)

                cand_dict = {
                    "track_id": row["track_id"],
                    "title": row["title"],
                    "artist": row["artist"],
                    "album": row["album"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "cover_url": row["cover_url"],
                    "duration_sec": row["duration_sec"],
                    "bpm": row["bpm"],
                    "energy_score": row["energy_score"],
                    "genres": track_genres,
                    "intro_vocal_start_sec": row["intro_vocal_start_sec"],
                    "outro_start_sec": row["outro_start_sec"],
                    "integrated_lufs": row["integrated_lufs"],
                    "musical_key": row["musical_key"] or "8A",
                    "first_downbeat_sec": float(row["first_downbeat_sec"] if "first_downbeat_sec" in row.keys() and row["first_downbeat_sec"] is not None else 0.0),
                    "beat_interval_sec": float(row["beat_interval_sec"] if "beat_interval_sec" in row.keys() and row["beat_interval_sec"] is not None else 0.5),
                }
                cand_mood = compute_track_mood(cand_dict).upper()
                cand_dict["mood"] = cand_mood

                # Dynamic Mood Vector Adjustment
                mood_adj = 0.0
                if pref_mood_upper and cand_mood == pref_mood_upper:
                    mood_adj += 1.8
                if cand_mood in disliked_mood_set or cand_mood in self.recent_early_skipped_moods:
                    mood_adj -= 2.2

                # Continuous Energy Match (|E_track - E_target|)
                cand_energy = float(row["energy_score"] or 0.6)
                energy_match = max(0.0, 1.0 - abs(cand_energy - self.session_target_energy) * 1.5)

                # Duration Sanity (sweet spot 120s - 360s)
                dur = float(row["duration_sec"] or 210.0)
                dur_score = 0.35 if 120.0 <= dur <= 360.0 else (-0.35 if dur > 420.0 else (-0.50 if dur < 90.0 else 0.0))

                # Favorite Boost
                fav_boost = float(row["favorite_boost"] or 1.0)
                fav_score = (fav_boost - 1.0) * 10.0

                total_score = (
                    (genre_score * 0.35)
                    + (b_bpm_match * 0.20)
                    + (energy_match * 0.25)
                    + (r_completion * 0.15)
                    + key_bonus
                    + mood_adj
                    + dur_score
                    + fav_score
                    + eps_discovery
                )

                cand_dict["score"] = round(total_score, 4)
                scored_candidates.append(cand_dict)

            scored_candidates.sort(key=lambda x: x["score"], reverse=True)

            # Deduplicate by primary artist
            unique_candidates = []
            seen = set()
            overflow = []
            for c in scored_candidates:
                a_norm = normalize_primary_artist(c["artist"])
                if a_norm not in seen:
                    seen.add(a_norm)
                    unique_candidates.append(c)
                else:
                    overflow.append(c)

            return (unique_candidates + overflow)[:limit]

    async def log_play_event(
        self,
        track_id: str,
        active_mood_card_id: str,
        listen_duration_sec: float,
        completion_ratio: float,
        action_taken: str,
    ) -> int:
        """
        Logs play telemetry and enforces the strict <50% skip penalty & 24h ban.
        """
        now = datetime.now()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            await db.execute(
                """
                INSERT INTO play_history (
                    track_id, hour_of_day, day_of_week, active_mood_card_id,
                    listen_duration_sec, completion_ratio, action_taken
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id,
                    now.hour,
                    now.weekday(),
                    active_mood_card_id,
                    listen_duration_sec,
                    completion_ratio,
                    action_taken,
                ),
            )

            # Fetch track metadata
            async with db.execute(
                "SELECT * FROM tracks WHERE track_id = ?", (track_id,)
            ) as cur:
                t_row = await cur.fetchone()

            if t_row:
                curr_skips = int(t_row["early_skip_count"] or 0)
                t_mood = compute_track_mood(dict(t_row)).upper()

                # Rule 1: Skip < 50% -> Day-ban or Vault
                if completion_ratio < 0.50 and "skipped" in action_taken:
                    new_skips = curr_skips + 1
                    # Track early-skipped mood in recent list to immediately shift station vibe
                    self.recent_early_skipped_moods.append(t_mood)
                    if len(self.recent_early_skipped_moods) > 6:
                        self.recent_early_skipped_moods.pop(0)

                    if new_skips >= 2:
                        # Vaulted for 14 days
                        await db.execute(
                            """
                            UPDATE tracks
                            SET early_skip_count = ?,
                                is_vaulted = 1,
                                cooldown_until = datetime('now', '+14 days'),
                                favorite_boost = MAX(0.5, favorite_boost - 0.3)
                            WHERE track_id = ?
                            """,
                            (new_skips, track_id),
                        )
                    else:
                        # 24-hour day-ban
                        await db.execute(
                            """
                            UPDATE tracks
                            SET early_skip_count = ?,
                                cooldown_until = datetime('now', '+24 hours'),
                                favorite_boost = MAX(0.7, favorite_boost - 0.15)
                            WHERE track_id = ?
                            """,
                            (new_skips, track_id),
                        )
                # Rule 2: Finished >= 80% -> reinforce mood vector and clear skip strikes
                elif completion_ratio >= 0.80:
                    self.session_target_bpm = (self.session_target_bpm * 0.7) + (float(t_row["bpm"]) * 0.3)
                    self.session_target_energy = (self.session_target_energy * 0.7) + (float(t_row["energy_score"]) * 0.3)
                    self.session_last_key = str(t_row["musical_key"] or "8A")

                    await db.execute(
                        """
                        UPDATE tracks
                        SET early_skip_count = 0,
                            favorite_boost = MIN(2.0, favorite_boost + 0.1)
                        WHERE track_id = ?
                        """,
                        (track_id,),
                    )

            await db.commit()
            return 0

    async def get_pinned_mood_cards(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM mood_cards WHERE is_pinned_to_car_wheel = 1 ORDER BY card_id ASC"
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_all_tracks(self, search: str = "", limit: int = 1000) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if search.strip():
                q = f"%{search.strip()}%"
                async with db.execute(
                    "SELECT * FROM tracks WHERE is_banned = 0 AND (title LIKE ? OR artist LIKE ? OR album LIKE ?) ORDER BY favorite_boost DESC, artist ASC LIMIT ?",
                    (q, q, q, limit),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM tracks WHERE is_banned = 0 ORDER BY favorite_boost DESC, artist ASC LIMIT ?",
                    (limit,),
                ) as cur:
                    rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["genres"] = json.loads(d.get("genres") or "[]")
                result.append(d)
            return result

    async def get_track_by_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                d = dict(row)
                d["genres"] = json.loads(d.get("genres") or "[]")
                return d
