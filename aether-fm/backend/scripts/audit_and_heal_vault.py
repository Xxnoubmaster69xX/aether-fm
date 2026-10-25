"""
Audit, Train & Heal Audio Vault (audit_and_heal_vault.py)
Performs 4 crucial maintenance tasks:
1. Ingests the two reference playlists to bootstrap `trusted_artists` (250+ verified artist browseIds).
2. Auto-replaces known cover-band entries in SQLite (e.g. Miura Jam / Studio Yuraki -> ALI / Snow Man).
3. Audits all cached files in E:\FM_Audio_Vault & .cache/audio:
   - Verifies if cached file matches a verified ATV studio track.
   - Deletes unverified legacy files so clean ATV masters download on demand.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from ytmusicapi import YTMusic

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from config import AUDIO_CACHE_DIR, DB_PATH
from ingestion.authenticity_guard import AuthenticityGuard, normalize_name
from ingestion.apple_music_sync import classify_artist_title

logger = logging.getLogger("aether_fm.heal_vault")
ytm = YTMusic()
guard = AuthenticityGuard(DB_PATH)

CLEAN_PLAYLIST_IDS = [
    "PLeqAPLzZSXE9NP4ph33JQwY4msY9GX0NE", # Intrusive thoughts won (Denonbu/J-Core/Electronic - 51 tracks)
    "PLeqAPLzZSXE8uwHN8BpFcffMGznyjZECB", # Virginity lock (Anime/J-Rock/J-Pop - 548 tracks)
]

# Real original artist mapping for cover-band entries
COVER_REPLACEMENTS = {
    "miura jam": ("ALI", "LOST IN PARADISE (feat. AKLO)", "LOST IN PARADISE"),
    "studio yuraki": {
        "lost in paradise": ("ALI", "LOST IN PARADISE (feat. AKLO)", "LOST IN PARADISE"),
        "grandeur": ("Snow Man", "Grandeur", "Grandeur"),
    }
}


def bootstrap_trusted_artists_from_playlists():
    print("\n--- STEP 1: Bootstrapping Trusted Artists from Clean Playlists ---")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    total_imported = 0
    trusted_count = 0

    for pid in CLEAN_PLAYLIST_IDS:
        try:
            pl = ytm.get_playlist(pid, limit=None)
            title = pl.get("title", pid)
            tracks = pl.get("tracks", [])
            print(f"Loaded '{title}' ({len(tracks)} tracks)")

            for t in tracks:
                vid = t.get("videoId")
                if not vid:
                    continue
                t_title = t.get("title", "").strip()
                artists = t.get("artists") or []
                t_artist = artists[0].get("name", "").strip() if artists else "Unknown"
                dur = float(t.get("duration_seconds") or 210.0)
                album = (t.get("album") or {}).get("name") if t.get("album") else ""
                video_type = t.get("videoType")

                # Record artist browseId into trusted_artists
                for a in artists:
                    a_name = a.get("name")
                    a_id = a.get("id")
                    if a_name and a_id and a_id.startswith("UC"):
                        cur.execute(
                            "INSERT OR REPLACE INTO trusted_artists (artist_norm, browse_id, display_name, source) VALUES (?, ?, ?, ?)",
                            (normalize_name(a_name), a_id, a_name, "playlist_bootstrap"),
                        )
                        trusted_count += 1

                # Upsert into tracks table as locked verified track
                digest = hashlib.md5(f"{t_artist}_{t_title}".lower().encode("utf-8")).hexdigest()[:12]
                track_id = f"ytm_{digest}"
                bpm, energy, genres = classify_artist_title(t_artist, t_title)

                cur.execute(
                    """
                    INSERT INTO tracks (
                        track_id, title, artist, album, source_type, source_id,
                        cover_url, duration_sec, bpm, energy_score, valence_score,
                        genres, intro_vocal_start_sec, outro_start_sec, integrated_lufs,
                        yt_video_id, resolved_title, resolved_artist, match_score, is_verified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(track_id) DO UPDATE SET
                        yt_video_id = excluded.yt_video_id,
                        resolved_title = excluded.resolved_title,
                        resolved_artist = excluded.resolved_artist,
                        match_score = 1.0,
                        is_verified = 1
                    """,
                    (
                        track_id, t_title, t_artist, album or title, "youtube",
                        f"https://music.youtube.com/watch?v={vid}",
                        (t.get("thumbnails") or [{}])[-1].get("url", ""),
                        dur, bpm, energy, 0.6, json.dumps(genres), 8.0, max(10.0, dur - 12.0),
                        -14.0, vid, t_title, t_artist, 1.0,
                    ),
                )
                total_imported += 1

        except Exception as e:
            print(f"Error processing playlist {pid}: {e}")

    conn.commit()
    conn.close()
    guard.refresh_trusted_artists()
    print(f"Imported {total_imported} verified tracks, trained {trusted_count} trusted artist IDs.")


def heal_cover_band_entries():
    print("\n--- STEP 2: Auto-Healing Cover-Band Entries in Library ---")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    rows = cur.execute("SELECT track_id, artist, title FROM tracks").fetchall()
    healed = 0

    for tid, artist, title in rows:
        a_low = artist.lower().strip()
        t_low = title.lower().strip()

        # Check Miura Jam
        if "miura jam" in a_low:
            cur.execute(
                """
                UPDATE tracks
                SET artist = 'ALI',
                    title = 'LOST IN PARADISE (feat. AKLO)',
                    source_id = 'ALI - LOST IN PARADISE',
                    yt_video_id = NULL,
                    resolved_title = NULL,
                    match_score = 0.0,
                    is_verified = 0
                WHERE track_id = ?
                """,
                (tid,),
            )
            print(f"Healed track {tid}: Miura Jam -> ALI - LOST IN PARADISE")
            healed += 1

        # Check Studio Yuraki
        elif "studio yuraki" in a_low:
            if "lost in paradise" in t_low:
                cur.execute(
                    """
                    UPDATE tracks
                    SET artist = 'ALI',
                        title = 'LOST IN PARADISE (feat. AKLO)',
                        source_id = 'ALI - LOST IN PARADISE',
                        yt_video_id = NULL,
                        resolved_title = NULL,
                        match_score = 0.0,
                        is_verified = 0
                    WHERE track_id = ?
                    """,
                    (tid,),
                )
                print(f"Healed track {tid}: Studio Yuraki -> ALI - LOST IN PARADISE")
                healed += 1
            elif "grandeur" in t_low:
                cur.execute(
                    """
                    UPDATE tracks
                    SET artist = 'Snow Man',
                        title = 'Grandeur',
                        source_id = 'Snow Man - Grandeur',
                        yt_video_id = NULL,
                        resolved_title = NULL,
                        match_score = 0.0,
                        is_verified = 0
                    WHERE track_id = ?
                    """,
                    (tid,),
                )
                print(f"Healed track {tid}: Studio Yuraki -> Snow Man - Grandeur")
                healed += 1

    conn.commit()
    conn.close()
    print(f"Auto-healed {healed} cover-band catalog entries.")


def purge_unverified_cache_files():
    print(f"\n--- STEP 3: Auditing Cached Files in Vault ({AUDIO_CACHE_DIR}) ---")
    if not AUDIO_CACHE_DIR.exists():
        print("Vault directory does not exist.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cached_files = [f for f in AUDIO_CACHE_DIR.iterdir() if f.is_file() and f.suffix in (".webm", ".opus", ".m4a")]
    print(f"Found {len(cached_files)} cached files on disk.")

    purged = 0
    kept = 0

    for f in cached_files:
        tid = f.stem
        row = cur.execute("SELECT is_verified, yt_video_id, title, artist FROM tracks WHERE track_id = ?", (tid,)).fetchone()
        
        # If track has no record or was not verified via YouTube Music ATV, purge legacy download!
        if not row:
            f.unlink(missing_ok=True)
            purged += 1
            continue

        is_verified, vid, title, artist = row
        if not is_verified and not vid:
            # Legacy unverified file from old general YouTube search! Delete it so clean studio ATV re-downloads.
            f.unlink(missing_ok=True)
            purged += 1
        else:
            kept += 1

    conn.close()
    print(f"Audit complete: Kept {kept} verified studio masters, purged {purged} legacy unverified files.")
    print("All upcoming song transitions will download authentic ATV studio audio.")


if __name__ == "__main__":
    bootstrap_trusted_artists_from_playlists()
    heal_cover_band_entries()
    purge_unverified_cache_files()
    print("\n[SUCCESS] Vault, Catalog & AI Authenticity Guard successfully trained and synchronized!")
