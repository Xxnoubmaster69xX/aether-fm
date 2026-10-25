"""
YouTube Music Playlist Importer (ytmusic_playlist.py)
Extracts official audio entries directly from YouTube Music playlists via yt-dlp flat extraction.
Stores exact video URLs (https://music.youtube.com/watch?v=...) as source_id, completely bypassing
search ambiguity and ensuring pristine audio without SFX or long video intros.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Tuple
import aiosqlite
import yt_dlp

from database.db_manager import DatabaseManager
from ingestion.apple_music_sync import classify_artist_title

logger = logging.getLogger("aether_fm.ytmusic_sync")


async def sync_ytmusic_playlist_url(playlist_url: str, db: DatabaseManager) -> int:
    """
    Extracts all tracks from a YouTube Music playlist or album URL using yt-dlp flat extraction.
    Inserts or updates the catalog in SQLite with exact videoIds and clean metadata.
    """
    clean_url = playlist_url.strip()
    if not clean_url:
        return 0

    ydl_opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
    except Exception as e:
        logger.error(f"Failed to extract playlist {playlist_url}: {e}")
        return 0

    if not info:
        return 0

    entries = info.get("entries") or []
    playlist_title = info.get("title") or "YouTube Music Import"

    rows_to_insert: List[Tuple[Any, ...]] = []

    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id") or entry.get("url")
        if not video_id:
            continue

        raw_title = (entry.get("title") or "").strip()
        raw_artist = (entry.get("uploader") or entry.get("artist") or "").strip()

        # Handle 'Artist - Title' title formatting common on YouTube uploads
        if " - " in raw_title and (not raw_artist or raw_artist.lower().endswith("topic")):
            parts = raw_title.split(" - ", 1)
            raw_artist = parts[0].strip()
            raw_title = parts[1].strip()

        # Strip " - Topic" suffix
        if raw_artist.lower().endswith(" - topic"):
            raw_artist = raw_artist[:-8].strip()

        if not raw_title:
            raw_title = f"Track {video_id}"
        if not raw_artist:
            raw_artist = "YouTube Music Artist"

        duration_sec = float(entry.get("duration") or 210.0)
        bpm, energy, genres = classify_artist_title(raw_artist, raw_title)

        digest = hashlib.md5(f"{raw_artist}_{raw_title}".lower().encode("utf-8")).hexdigest()[:12]
        track_id = f"ytm_{digest}"
        direct_url = f"https://music.youtube.com/watch?v={video_id}"
        thumbnail = entry.get("thumbnail") or ""

        rows_to_insert.append(
            (
                track_id,
                raw_title,
                raw_artist,
                playlist_title,
                "youtube",
                direct_url,
                thumbnail,
                duration_sec,
                bpm,
                energy,
                0.6,
                json.dumps(genres),
                8.0,
                max(10.0, duration_sec - 12.0),
                -14.0,
                video_id,
                raw_title,
                raw_artist,
                1.0,
            )
        )

    if not rows_to_insert:
        return 0

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.executemany(
            """
            INSERT INTO tracks (
                track_id, title, artist, album, source_type, source_id,
                cover_url, duration_sec, bpm, energy_score, valence_score,
                genres, intro_vocal_start_sec, outro_start_sec, integrated_lufs,
                yt_video_id, resolved_title, resolved_artist, match_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                source_id = excluded.source_id,
                yt_video_id = excluded.yt_video_id,
                resolved_title = excluded.resolved_title,
                resolved_artist = excluded.resolved_artist,
                match_score = 1.0,
                bpm = excluded.bpm,
                energy_score = excluded.energy_score,
                genres = excluded.genres
            """,
            rows_to_insert,
        )
        await conn.commit()

    return len(rows_to_insert)
