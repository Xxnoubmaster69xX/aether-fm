"""
Preload & Vault Downloader for 6 Stations (scripts/preload_stations.py)
Downloads official studio tracks from YouTube Music and populates E:\\FM_Audio_Vault
with high-priority songs across:
- Rock & Metal
- Country & Heartland
- J-Pop & Tokyo Groove
- J-Core & Eurobeat
- EDM & Festival Bass
- Game OST & Boss Themes
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sys

# Ensure backend root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import aiosqlite
from config import AUDIO_CACHE_DIR, DB_PATH
from ingestion.ytdlp_client import YtDlpCacheResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("preload")

# Curated high-priority official studio tracks for stations that need catalog seeding
CURATED_STATION_TRACKS = [
    # 1. Country & Heartland
    {"artist": "Morgan Wallen", "title": "Last Night", "genres": ["country", "americana"]},
    {"artist": "Luke Combs", "title": "Fast Car", "genres": ["country", "acoustic", "folk"]},
    {"artist": "Zach Bryan", "title": "Something in the Orange", "genres": ["country", "americana", "folk"]},
    {"artist": "Chris Stapleton", "title": "Tennessee Whiskey", "genres": ["country", "southern rock"]},
    {"artist": "Luke Combs", "title": "When It Rains It Pours", "genres": ["country"]},
    {"artist": "Morgan Wallen", "title": "You Proof", "genres": ["country"]},
    {"artist": "Zach Bryan", "title": "I Remember Everything", "genres": ["country", "folk"]},
    {"artist": "Post Malone", "title": "I Had Some Help", "genres": ["country", "americana"]},
    {"artist": "Chris Stapleton", "title": "White Horse", "genres": ["country", "southern rock"]},
    {"artist": "Cody Johnson", "title": "'Til You Can't", "genres": ["country"]},

    # 2. Game OST & Boss Themes
    {"artist": "HOYO-MiX", "title": "Wildfire", "genres": ["ost", "soundtrack", "game", "rock"]},
    {"artist": "Casey Edwards", "title": "Bury the Light", "genres": ["ost", "game", "metal"]},
    {"artist": "Casey Edwards", "title": "Devil Trigger", "genres": ["ost", "game", "electronic"]},
    {"artist": "HOYO-MiX", "title": "Nevermore", "genres": ["ost", "soundtrack", "game"]},
    {"artist": "Masayoshi Soken", "title": "To the Edge", "genres": ["ost", "game", "rock"]},
    {"artist": "Masayoshi Soken", "title": "Flow", "genres": ["ost", "game", "cinematic"]},
    {"artist": "Nobuo Uematsu", "title": "One-Winged Angel", "genres": ["ost", "game", "cinematic"]},
    {"artist": "Sega Sound Team", "title": "Undefeatable", "genres": ["ost", "game", "metal", "rock"]},
    {"artist": "Atlus Sound Team", "title": "Rivers in the Desert", "genres": ["ost", "game", "rock"]},
    {"artist": "HOYO-MiX", "title": "Hope Is the Thing With Feathers", "genres": ["ost", "game", "pop"]},

    # 3. EDM & Festival Bass
    {"artist": "Martin Garrix", "title": "Starlight (Keep Me Afloat)", "genres": ["edm", "dance", "electronic", "house"]},
    {"artist": "Porter Robinson", "title": "Language", "genres": ["edm", "electronic", "dance"]},
    {"artist": "Avicii", "title": "Levels", "genres": ["edm", "house", "dance"]},
    {"artist": "Madeon", "title": "Shelter", "genres": ["edm", "electronic", "pop"]},
    {"artist": "Knock2", "title": "dashstar*", "genres": ["edm", "bass house", "dance"]},
    {"artist": "ODESZA", "title": "Sun Models", "genres": ["edm", "electronic", "ambient"]},
    {"artist": "Kygo", "title": "Firestone", "genres": ["edm", "house", "dance"]},
    {"artist": "Martin Garrix", "title": "Animals", "genres": ["edm", "electronic", "house"]},
    {"artist": "Brooks", "title": "Byte", "genres": ["edm", "future house", "dance"]},
    {"artist": "Zedd", "title": "Clarity", "genres": ["edm", "electronic", "dance"]},

    # 4. J-Core & Eurobeat
    {"artist": "Dave Rodgers", "title": "Deja Vu", "genres": ["eurobeat", "dance", "electronic"]},
    {"artist": "Max Coveri", "title": "Running in the 90's", "genres": ["eurobeat", "dance"]},
    {"artist": "Manuel", "title": "Gas Gas Gas", "genres": ["eurobeat", "dance"]},
    {"artist": "USAO", "title": "Cthulhu", "genres": ["j-core", "hard dance"]},
    {"artist": "t+pazolite", "title": "Marunouchi Survivor", "genres": ["j-core", "speedcore"]},
    {"artist": "Camellia", "title": "GHOST", "genres": ["j-core", "electronic"]},
    {"artist": "Srav3R", "title": "Hi-Tech Rave", "genres": ["j-core", "hardcore"]},
    {"artist": "DENONBU", "title": "Maji-Karu", "genres": ["j-core", "electronic", "dance"]},

    # 5. Rock & Metal
    {"artist": "Linkin Park", "title": "Numb", "genres": ["rock", "alternative"]},
    {"artist": "ONE OK ROCK", "title": "The Beginning", "genres": ["rock", "j-rock"]},
    {"artist": "Coldrain", "title": "Mayday", "genres": ["rock", "metal", "post-hardcore"]},
    {"artist": "Skillet", "title": "Monster", "genres": ["rock", "metal"]},
    {"artist": "BAND-MAID", "title": "Thrill", "genres": ["rock", "j-rock", "metal"]},

    # 6. J-Pop & Tokyo Groove
    {"artist": "YOASOBI", "title": "Idol", "genres": ["j-pop", "anime", "pop"]},
    {"artist": "Ado", "title": "Show", "genres": ["j-pop", "dance", "pop"]},
    {"artist": "Hoshimachi Suisei", "title": "Stellar Stellar", "genres": ["j-pop", "electronic"]},
    {"artist": "natori", "title": "Overdose", "genres": ["j-pop", "tokyo groove"]},
    {"artist": "Vaundy", "title": "Kaiju no Hanauta", "genres": ["j-pop", "rock"]},
    {"artist": "Kenshi Yonezu", "title": "KICK BACK", "genres": ["j-pop", "rock", "anime"]},
    {"artist": "Eve", "title": "Kaikai Kitan", "genres": ["j-pop", "j-rock", "anime"]},
]


async def seed_curated_into_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for t in CURATED_STATION_TRACKS:
            raw_id = f"custom_{t['artist']}_{t['title']}".lower().replace(" ", "_")
            track_id = f"am_{hashlib.md5(raw_id.encode('utf-8')).hexdigest()[:12]}"
            await db.execute(
                """
                INSERT INTO tracks (
                    track_id, title, artist, source_type, source_id,
                    genres, duration_sec, bpm, energy_score
                ) VALUES (?, ?, ?, 'youtube', ?, ?, 210.0, 128.0, 0.7)
                ON CONFLICT(track_id) DO UPDATE SET
                    genres = excluded.genres
                """,
                (track_id, t["title"], t["artist"], track_id, json.dumps(t["genres"])),
            )
        await db.commit()
    logger.info("Curated tracks registered in database.")


async def preload_target_songs(target_count: int = 50) -> None:
    resolver = YtDlpCacheResolver(AUDIO_CACHE_DIR)
    await seed_curated_into_db()

    cached_ids = set(resolver._cached_track_ids)
    logger.info(f"Current vault cached tracks: {len(cached_ids)}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Pick from curated first, then top un-cached tracks in the DB
        async with db.execute(
            """
            SELECT track_id, title, artist, duration_sec, genres
            FROM tracks
            ORDER BY favorite_boost DESC, bpm ASC
            """
        ) as cur:
            all_tracks = await cur.fetchall()

    needed = [t for t in all_tracks if t["track_id"] not in cached_ids]
    logger.info(f"Tracks available to download: {len(needed)}. Targeting: {target_count}")

    downloaded = 0
    for t in needed:
        if downloaded >= target_count:
            break
        logger.info(f"[{downloaded+1}/{target_count}] Downloading '{t['artist']} - {t['title']}'...")
        path = await asyncio.to_thread(
            resolver.resolve_and_cache,
            t["track_id"],
            f"{t['artist']} - {t['title']}",
            expected_duration_sec=float(t["duration_sec"] or 210.0),
            artist=t["artist"],
            title=t["title"],
        )
        if path and path.exists() and path.stat().st_size > 10000:
            downloaded += 1
            logger.info(f" -> SUCCESS: Saved to {path.name} ({path.stat().st_size // 1024} KB)")
        else:
            logger.warning(f" -> SKIPPED/FAILED for '{t['artist']} - {t['title']}'")

    resolver._rescan_cache_index()
    logger.info(f"Done! Vault now contains {len(resolver._cached_track_ids)} verified local studio tracks.")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    asyncio.run(preload_target_songs(target_count=count))
